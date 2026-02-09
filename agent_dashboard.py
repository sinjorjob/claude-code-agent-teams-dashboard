"""
Claude Code Agent Teams ダッシュボード V1.0
==========================================
Agent Teamsの状態をリアルタイムに監視する正規版ダッシュボード。

主要機能:
- チームの自動検出（~/.claude/teams/）
- config.jsonからのメンバー動的読み込み
- タスクファイルの監視（status, owner, blockedBy対応）
- Inboxメッセージの監視と通信ログ表示
- タスク依存関係の表示
- ファイル変更の監視

使い方:
    python agent_dashboard_v1.py --watch-dir ./demo
    python agent_dashboard_v1.py --team self-intro-web --watch-dir ./demo
    python agent_dashboard_v1.py --demo
"""

import argparse
import json
import os
import sys
import time
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

# キーボード入力検出用（プラットフォーム依存）
try:
    import msvcrt  # Windows
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False
    try:
        import termios  # Unix/Linux
        import tty
        HAS_TERMIOS = True
    except ImportError:
        HAS_TERMIOS = False

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress_bar import ProgressBar
from rich import box
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# ─── データクラス ───────────────────────────────────────────

class AgentInfo:
    """1人のエージェントメンバーの状態を保持"""

    # エージェント固有カラー（config.jsonのcolorフィールドに対応）
    COLOR_MAP = {
        "blue": "#3b82f6",
        "green": "#10b981",
        "yellow": "#f59e0b",
        "red": "#ef4444",
        "purple": "#a855f7",
        "orange": "#f97316",
        "pink": "#ec4899",
        "cyan": "#06b6d4",
    }

    STATUS_STYLES = {
        "待機中": ("dim",     "○"),
        "作業中": ("#22d3ee", "◉"),
        "完了":   ("#4ade80", "●"),
        "エラー": ("#f87171", "✖"),
        "稼働中": ("#60a5fa", "◉"),
    }

    def __init__(self, agent_id: str, name: str, agent_type: str = "general-purpose",
                 color: str = "blue", model: str = "sonnet"):
        self.agent_id = agent_id
        self.name = name
        self.agent_type = agent_type
        self.color_name = color
        self.model = model
        self.status = "待機中"
        self.current_task = "タスク待ち"
        self.assigned_task_id: Optional[str] = None
        self.assigned_task_subject: Optional[str] = None
        self.progress = 0
        self.last_updated = datetime.now()
        self.files_touched: list[str] = []

    @property
    def icon(self) -> str:
        """アイコン（名前の最初の2文字）"""
        if not self.name:
            return "•"
        return self.name[:2].upper() if len(self.name) >= 2 else self.name[0].upper()

    @property
    def color(self) -> str:
        """色コード"""
        return self.COLOR_MAP.get(self.color_name, "#3b82f6")

    @property
    def status_icon(self) -> str:
        """ステータスアイコン"""
        return self.STATUS_STYLES.get(self.status, ("dim", "○"))[1]

    @property
    def status_color(self) -> str:
        """ステータスカラー"""
        return self.STATUS_STYLES.get(self.status, ("dim", "○"))[0]


class MessageLog:
    """エージェント間メッセージの管理"""

    def __init__(self, max_entries: int = 100):
        self.entries: deque = deque(maxlen=max_entries)
        self.lock = threading.Lock()

    def add(self, from_agent: str, to_agent: str, text: str,
            msg_type: str = "message", timestamp: str = None,
            from_color: str = None, to_color: str = None):
        """メッセージを追加"""
        with self.lock:
            if timestamp is None:
                timestamp = datetime.now().strftime("%H:%M:%S")

            self.entries.append({
                "timestamp": timestamp,
                "from": from_agent,
                "to": to_agent,
                "text": text,
                "type": msg_type,
                "from_color": from_color,
                "to_color": to_color,
            })

    def get_recent(self, count: int = 15) -> list:
        """最新メッセージを取得（タイムスタンプ順にソート）"""
        with self.lock:
            # 全メッセージをタイムスタンプ順にソート
            sorted_entries = sorted(self.entries, key=lambda x: x.get("timestamp", ""))
            # 最新count件を返す
            return sorted_entries[-count:]


class TaskStore:
    """共有タスクリストの管理"""

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.lock = threading.Lock()

    def update(self, task_id: str, data: dict):
        """タスクを追加/更新"""
        with self.lock:
            self.tasks[task_id] = data

    def get_all(self) -> list[dict]:
        """全タスクをID順で取得"""
        with self.lock:
            return [
                {"id": k, **v}
                for k, v in sorted(self.tasks.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999)
            ]

    def get_blocked_info(self, task_id: str) -> str:
        """依存関係の表示文字列を生成"""
        with self.lock:
            task = self.tasks.get(task_id, {})
            blocked_by = task.get("blockedBy", [])
            if blocked_by:
                return f" ← blocked by {', '.join(f'#{bid}' for bid in blocked_by)}"
            return ""


# ─── チーム自動検出 ─────────────────────────────────────────

class TeamDiscovery:
    """~/.claude/teams/ からチームを自動検出"""

    def __init__(self, claude_dir: str):
        self.claude_dir = Path(claude_dir)
        self.teams_dir = self.claude_dir / "teams"

    def find_active_team(self) -> Optional[tuple[str, dict]]:
        """最新の config.json を持つチームを返す"""
        if not self.teams_dir.exists():
            return None

        teams = []
        for team_dir in self.teams_dir.iterdir():
            if team_dir.is_dir():
                config_path = team_dir / "config.json"
                if config_path.exists():
                    teams.append((team_dir.name, config_path))

        if not teams:
            return None

        # 最新のconfig.jsonを持つチームを選択
        latest = max(teams, key=lambda x: x[1].stat().st_mtime)
        team_name = latest[0]
        config = self.load_team_config(team_name)

        return (team_name, config) if config else None

    def load_team_config(self, team_name: str) -> Optional[dict]:
        """config.json を読み込んでメンバー一覧を返す"""
        config_path = self.teams_dir / team_name / "config.json"

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Warning: Failed to load config.json: {e}", file=sys.stderr)
            return None

    def build_agents(self, config: dict) -> dict[str, AgentInfo]:
        """config.json の members から AgentInfo 辞書を構築"""
        agents = {}

        # 色のローテーションリスト（重複を避ける）
        color_rotation = ["blue", "green", "yellow", "red", "purple", "cyan", "magenta", "orange"]
        used_colors = set()

        for idx, member in enumerate(config.get("members", [])):
            name = member.get("name", "unknown")
            agent_id = member.get("agentId", name)
            agent_type = member.get("agentType", "general-purpose")
            model = member.get("model", "sonnet")

            # 色の割り当て: config.jsonに色があればそれを使用、なければローテーションから未使用の色を選択
            color = member.get("color", "")
            if not color or color in used_colors:
                # 未使用の色を探す
                for c in color_rotation:
                    if c not in used_colors:
                        color = c
                        break
                else:
                    # 全色使用済みの場合はインデックスベースで選択
                    color = color_rotation[idx % len(color_rotation)]

            used_colors.add(color)

            agents[name] = AgentInfo(
                agent_id=agent_id,
                name=name,
                agent_type=agent_type,
                color=color,
                model=model
            )

        return agents

    def discover_past_agents_from_inboxes(self, team_name: str, current_agents: dict[str, AgentInfo]) -> dict[str, AgentInfo]:
        """inboxファイルから過去のエージェントを検出して復元"""
        inbox_dir = self.teams_dir / team_name / "inboxes"

        if not inbox_dir.exists():
            return current_agents

        # 既存のエージェント名のセット
        current_names = set(current_agents.keys())

        # inboxファイルを走査
        for inbox_file in inbox_dir.glob("*.json"):
            if inbox_file.name.startswith("."):
                continue

            agent_name = inbox_file.stem

            # 既に存在するエージェントはスキップ
            if agent_name in current_names:
                continue

            # inboxファイルからエージェント情報を復元
            try:
                with open(inbox_file, "r", encoding="utf-8") as f:
                    messages = json.load(f)

                if not isinstance(messages, list) or len(messages) == 0:
                    continue

                # 最後のメッセージから情報を取得
                last_msg = messages[-1]

                # 色情報を取得（メッセージ内のcolorフィールドまたはfromエージェントの色）
                color = None
                for msg in reversed(messages):
                    if msg.get("color"):
                        color = msg["color"]
                        break

                if not color:
                    # デフォルト色
                    color = "white"

                # 最終アクティビティ時刻
                last_timestamp = last_msg.get("timestamp", "")
                if last_timestamp and "T" in last_timestamp:
                    try:
                        dt = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
                        dt_local = dt.astimezone()
                        last_updated = dt_local
                    except:
                        last_updated = datetime.now()
                else:
                    last_updated = datetime.now()

                # 最後のメッセージタイプからステータスを推測
                last_text = last_msg.get("text", "")
                status = "完了"  # デフォルト
                current_task = "完了"
                progress = 100

                if "shutdown_approved" in last_text or "shutdown_ack" in last_text:
                    status = "完了"
                    current_task = "シャットダウン済み"
                elif "待機中" in last_text or "idle" in last_text:
                    status = "完了"
                    current_task = "待機後終了"
                elif "完了" in last_text or "completed" in last_text:
                    status = "完了"
                    current_task = "タスク完了"

                # AgentInfoを作成（非アクティブエージェント）
                agent_info = AgentInfo(
                    agent_id=f"{agent_name}@{team_name}",
                    name=agent_name,
                    agent_type="general-purpose",
                    color=color,
                    model="unknown"
                )
                agent_info.status = status
                agent_info.current_task = current_task
                agent_info.progress = progress
                agent_info.last_updated = last_updated

                current_agents[agent_name] = agent_info

            except (OSError, json.JSONDecodeError) as e:
                continue

        return current_agents


# ─── ファイルシステム監視 ───────────────────────────────────

class ConfigWatcher(FileSystemEventHandler):
    """config.json の変更を監視してエージェントリストを動的更新"""

    def __init__(self, agents: dict[str, AgentInfo], team_discovery: TeamDiscovery, team_name: str):
        self.agents = agents
        self.team_discovery = team_discovery
        self.team_name = team_name

    def on_modified(self, event):
        """config.json の更新を検知"""
        if event.is_directory or not event.src_path.endswith("config.json"):
            return

        # config.json を再読み込み
        config = self.team_discovery.load_team_config(self.team_name)
        if not config:
            return

        # 新しいエージェントリストを構築
        new_agents = self.team_discovery.build_agents(config)

        # 既存エージェントの状態を保持しながら更新
        for name, new_agent in new_agents.items():
            if name in self.agents:
                # 既存エージェント: 状態情報を保持
                old_agent = self.agents[name]
                new_agent.status = old_agent.status
                new_agent.current_task = old_agent.current_task
                new_agent.assigned_task_id = old_agent.assigned_task_id
                new_agent.assigned_task_subject = old_agent.assigned_task_subject
                new_agent.progress = old_agent.progress
                new_agent.last_updated = old_agent.last_updated
                new_agent.files_touched = old_agent.files_touched

            self.agents[name] = new_agent


class InboxWatcher(FileSystemEventHandler):
    """~/.claude/teams/{name}/inboxes/ のメッセージを監視"""

    def __init__(self, message_log: MessageLog, agents: dict[str, AgentInfo]):
        self.message_log = message_log
        self.agents = agents
        self.read_positions: dict[str, int] = {}  # agent → 既読位置
        self.lock = threading.Lock()

    def on_created(self, event):
        """inbox ファイルの作成を検知"""
        self.on_modified(event)

    def on_modified(self, event):
        """inbox ファイルの変更を検知し、差分メッセージを抽出"""
        if event.is_directory or not event.src_path.endswith(".json"):
            return

        # .lock等のシステムファイルを無視
        filename = Path(event.src_path).name
        if filename.startswith("."):
            return

        agent_name = Path(event.src_path).stem
        new_messages = self._parse_inbox(event.src_path, agent_name)

        for msg in new_messages:
            from_agent = msg.get("from", "unknown")
            text = msg.get("text", "")
            timestamp = msg.get("timestamp", "")

            # 自分から自分へのメッセージはスキップ（タスク割り当て等の内部メッセージ）
            if from_agent == agent_name:
                continue

            # タイムスタンプをHH:MM:SS形式に変換（ローカル時刻に変換）
            if timestamp and "T" in timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    # UTCからローカル時刻に変換
                    dt_local = dt.astimezone()
                    timestamp_str = dt_local.strftime("%H:%M:%S")
                except:
                    timestamp_str = timestamp[:8]
            else:
                timestamp_str = datetime.now().strftime("%H:%M:%S")

            # 構造化メッセージをパース
            msg_type = "message"
            if text.startswith("{"):
                try:
                    structured = json.loads(text)
                    msg_type = structured.get("type", "message")

                    # 特定のメッセージタイプを整形
                    if msg_type == "task_assignment":
                        text = f"[タスク割当] {structured.get('subject', '')}"
                    elif msg_type == "idle_notification":
                        text = "[待機中]"
                    elif msg_type == "task_completed":
                        text = f"[✓] タスク#{structured.get('taskId', '')}完了"
                    elif msg_type == "shutdown_request":
                        text = "[シャットダウン要求]"
                    elif msg_type == "shutdown_ack":
                        text = "[シャットダウン確認]"
                    else:
                        # その他の構造化メッセージも整形（JSONをそのまま表示しない）
                        text = f"[{msg_type}]"
                except json.JSONDecodeError:
                    pass

            # サマリーがあればそれを優先
            summary = msg.get("summary", "")
            if summary:
                text = summary

            # 送信元エージェントの状態を更新
            if from_agent in self.agents:
                sender = self.agents[from_agent]
                sender.last_updated = datetime.now()
                if msg_type == "idle_notification":
                    sender.status = "待機中"
                    sender.progress = 0
                elif msg_type == "shutdown_ack" or msg_type == "shutdown_request":
                    sender.status = "完了"
                    sender.progress = 100
                elif msg_type == "task_completed":
                    sender.status = "完了"
                    sender.progress = 100
                else:
                    sender.status = "稼働中"
                    sender.progress = 50

            # 受信者エージェントの状態を更新（メッセージを受信 = 稼働中）
            if agent_name in self.agents:
                receiver = self.agents[agent_name]
                receiver.last_updated = datetime.now()
                if receiver.status not in ["完了"]:
                    receiver.status = "稼働中"
                    if receiver.progress < 50:
                        receiver.progress = 50

            # 色情報を取得
            from_color = msg.get("color", None)

            # 受信者（to）の色はagentsから取得
            to_color = None
            if agent_name in self.agents:
                to_color = self.agents[agent_name].color_name

            self.message_log.add(
                from_agent=from_agent,
                to_agent=agent_name,
                text=text[:60],  # 60文字に制限
                msg_type=msg_type,
                timestamp=timestamp_str,
                from_color=from_color,
                to_color=to_color
            )

    def _parse_inbox(self, filepath: str, agent_name: str) -> list[dict]:
        """inbox JSONを読み込み、新着メッセージを返す"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                messages = json.load(f)

            if not isinstance(messages, list):
                return []

            # 既読位置を取得
            with self.lock:
                last_pos = self.read_positions.get(agent_name, 0)
                new_messages = messages[last_pos:]
                self.read_positions[agent_name] = len(messages)

            return new_messages

        except (OSError, json.JSONDecodeError) as e:
            return []


class TaskWatcher(FileSystemEventHandler):
    """~/.claude/tasks/{name}/ のタスクファイルを監視"""

    def __init__(self, task_store: TaskStore, agents: dict[str, AgentInfo]):
        self.task_store = task_store
        self.agents = agents

    def on_created(self, event):
        """タスクファイルの作成を検知"""
        self._handle_task_change(event)

    def on_modified(self, event):
        """タスクファイルの変更を検知"""
        self._handle_task_change(event)

    def _handle_task_change(self, event):
        """タスクファイルの変更を処理"""
        if event.is_directory or not event.src_path.endswith(".json"):
            return

        # .lock, .highwatermark は無視
        filename = Path(event.src_path).name
        if filename.startswith("."):
            return

        task_data = self._parse_task(event.src_path)
        if task_data:
            task_id = task_data.get("id", "")

            # 内部タスク（metadata._internal）は無視
            if task_data.get("metadata", {}).get("_internal"):
                return

            # タスクストアに追加
            self.task_store.update(task_id, task_data)

            # エージェントの状態を更新
            owner = task_data.get("owner", "")
            status = task_data.get("status", "pending")
            subject = task_data.get("subject", "")

            if owner and owner in self.agents:
                agent = self.agents[owner]
                agent.assigned_task_id = task_id
                agent.assigned_task_subject = subject

                if status == "in_progress":
                    agent.status = "作業中"
                    agent.current_task = subject
                    agent.progress = 50
                elif status == "completed":
                    agent.status = "完了"
                    agent.progress = 100
                else:
                    agent.status = "待機中"
                    agent.progress = 0

                agent.last_updated = datetime.now()

    def _parse_task(self, filepath: str) -> Optional[dict]:
        """タスクJSONを読み込み"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None


class FileChangeWatcher(FileSystemEventHandler):
    """プロジェクトディレクトリのファイル変更を監視"""

    def __init__(self, file_log: deque, agents: dict[str, AgentInfo]):
        self.file_log = file_log
        self.agents = agents
        self.lock = threading.Lock()

    def on_created(self, event):
        if not event.is_directory:
            self._add_event("作成", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._add_event("更新", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._add_event("削除", event.src_path)

    def _add_event(self, event_type: str, filepath: str):
        """ファイル変更イベントを記録"""
        filename = Path(filepath).name

        # 特定のファイルは無視
        if filename.startswith(".") or filename.endswith((".pyc", ".log", "__pycache__")):
            return

        with self.lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.file_log.append({
                "timestamp": timestamp,
                "type": event_type,
                "file": filename
            })


# ─── ダッシュボードUI ───────────────────────────────────────

class Dashboard:
    """Agent Teamsダッシュボード"""

    def __init__(self, team_name: str, agents: dict[str, AgentInfo],
                 task_store: TaskStore, message_log: MessageLog, file_log: deque):
        self.team_name = team_name
        self.agents = agents
        self.task_store = task_store
        self.message_log = message_log
        self.file_log = file_log
        self.start_time = datetime.now()
        self.console = Console()
        self._layout = None  # Layoutオブジェクトのキャッシュ
        self._footer_panel = None  # フッターのキャッシュ

    def make_layout(self) -> Layout:
        """レイアウトを構築（コンテンツに応じて動的にサイズ調整）"""
        layout = Layout()

        # エージェント数とタスク数を取得
        agent_count = len(self.agents)
        tasks = self.task_store.get_all()
        task_count = len([t for t in tasks if not t.get("metadata", {}).get("_internal")])
        message_count = len(self.message_log.entries)
        file_count = len(self.file_log)

        # エージェントパネルの必要行数（1エージェント = 1行、最低5行）
        agent_lines = max(agent_count + 2, 5)  # +2はヘッダーとマージン

        # タスクパネルの必要行数（1タスク = 1行、最低5行）
        task_lines = max(task_count + 2, 5)  # +2はヘッダーとマージン

        # body の必要行数（エージェントとタスクの大きい方）
        body_lines = max(agent_lines, task_lines)

        # 通信ログとファイル変更ログの必要行数
        comm_lines = min(message_count + 2, 20)  # 最大20行
        file_lines = min(file_count + 2, 20)  # 最大20行
        bottom_lines = max(comm_lines, file_lines, 8)  # 最低8行

        # 比率を計算（行数ベース）
        body_ratio = max(body_lines, 10)
        bottom_ratio = max(bottom_lines, 6)

        # フッターを含むレイアウト
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=body_ratio),
            Layout(name="bottom", ratio=bottom_ratio),
            Layout(name="footer", size=3),  # フッター（メニューバー）
        )

        layout["body"].split_row(
            Layout(name="agents", ratio=1),
            Layout(name="tasks", ratio=1),
        )

        layout["bottom"].split_row(
            Layout(name="comms", ratio=1),
            Layout(name="files", ratio=1),
        )

        return layout

    def render(self) -> Layout:
        """全体を描画"""
        # 初回のみLayoutを作成
        if self._layout is None:
            self._layout = self.make_layout()

        # ダッシュボード部分を更新
        self._layout["header"].update(self._make_header())
        self._layout["agents"].update(self._make_agents_panel())
        self._layout["tasks"].update(self._make_tasks_panel())
        self._layout["comms"].update(self._make_comms_panel())
        self._layout["files"].update(self._make_files_panel())

        # フッターは初回のみ作成・設定（以降は更新しない）
        if self._footer_panel is None:
            self._footer_panel = self._make_footer_panel()
            self._layout["footer"].update(self._footer_panel)

        return self._layout

    def _make_header(self) -> Panel:
        """ヘッダーパネル"""
        elapsed = datetime.now() - self.start_time
        elapsed_str = f"{int(elapsed.total_seconds() // 60):02d}:{int(elapsed.total_seconds() % 60):02d}"

        # 稼働中のエージェント数
        active_count = sum(1 for a in self.agents.values() if a.status in ["作業中", "稼働中"])

        # タスク完了数
        tasks = self.task_store.get_all()
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        total = len([t for t in tasks if not t.get("metadata", {}).get("_internal")])

        header_text = Text()
        header_text.append("◆ CLAUDE CODE AGENT TEAMS ◆\n", style="bold cyan")
        header_text.append(f"⠋ チーム: {self.team_name} │ ", style="dim")
        header_text.append(f"⏱ {elapsed_str} │ ", style="yellow")
        header_text.append(f"● {active_count}稼働 ", style="green")
        header_text.append(f"✓ {completed}/{total}完了", style="cyan")

        return Panel(header_text, border_style="cyan")

    def _make_agents_panel(self) -> Panel:
        """エージェント状態パネル"""
        table = Table(show_header=False, box=None, padding=(0, 0))
        table.add_column("状態", style="bold")

        for agent in self.agents.values():
            # 非アクティブなエージェント（過去に存在したがシャットダウン済み）を判定
            is_active = agent.model != "unknown"

            # エージェント行
            status_icon = agent.status_icon
            status_color = agent.status_color

            # プログレスバー（罫線文字+グラデーション色で描画）
            bar_width = 16
            filled = int(bar_width * agent.progress / 100)
            empty = bar_width - filled

            # ステータス表示（全角文字の幅を考慮してパディング）
            # 「待機中」「作業中」「稼働中」= 3文字(6幅)、「完了」= 2文字(4幅)、「エラー」= 3文字(6幅)
            status_text = agent.status
            # 全角文字数を数えて半角スペースでパディング
            display_width = sum(2 if ord(c) > 127 else 1 for c in status_text)
            status_padded = status_text + " " * (8 - display_width)

            # タスク情報
            task_info = ""
            if agent.assigned_task_subject:
                task_info = f"📋 #{agent.assigned_task_id} {agent.assigned_task_subject}"
            elif agent.current_task:
                task_info = agent.current_task

            # タイムスタンプ
            time_str = agent.last_updated.strftime("%H:%M:%S")

            # 行を構築（非アクティブなエージェントはdimスタイル）
            row = Text()
            if is_active:
                row.append(f"▍{agent.icon} ", style=agent.color)
                row.append(f"{agent.name:15s} ", style="bold")
                row.append(f"{status_icon} ", style=status_color)
                row.append(status_padded, style=status_color)
                # ステータスに応じたバーの色（罫線文字で描画）
                if agent.progress == 0:
                    row.append("─" * bar_width, style="dim")
                elif agent.progress >= 100:
                    row.append("━" * bar_width, style="bold #4ade80")
                else:
                    if agent.progress < 30:
                        bar_color = "#f59e0b"
                    elif agent.progress < 60:
                        bar_color = "#22d3ee"
                    elif agent.progress < 90:
                        bar_color = "#818cf8"
                    else:
                        bar_color = "#4ade80"
                    row.append("━" * filled, style=f"bold {bar_color}")
                    row.append("╸", style=bar_color)
                    if empty > 1:
                        row.append("─" * (empty - 1), style="dim")
                row.append(f" {agent.progress:3d}% ", style=status_color)
                row.append(f"{task_info:30s} ", style="dim")
                row.append(f"{time_str}", style="dim")
            else:
                # 非アクティブ: 全体をdimスタイルで表示
                row.append(f"▍{agent.icon} ", style=f"dim {agent.color}")
                row.append(f"{agent.name:15s} ", style="dim")
                row.append(f"{status_icon} ", style="dim")
                row.append(status_padded, style="dim")
                row.append("─" * bar_width, style="dim")
                row.append(f" {agent.progress:3d}% ", style="dim")
                row.append(f"{task_info:30s} ", style="dim")
                row.append(f"{time_str}", style="dim")

            table.add_row(row)

            # ファイル情報（サブ行）
            if agent.files_touched:
                files_str = ", ".join(agent.files_touched[-3:])
                file_row = Text()
                file_row.append(f"   └─ 📄 {files_str}", style="dim italic")
                table.add_row(file_row)

        return Panel(table, title="◉ エージェント状態", border_style="blue")

    def _make_tasks_panel(self) -> Panel:
        """タスクリストパネル"""
        tasks = self.task_store.get_all()

        # 内部タスクを除外
        tasks = [t for t in tasks if not t.get("metadata", {}).get("_internal")]

        completed = sum(1 for t in tasks if t.get("status") == "completed")
        total = len(tasks)

        # 担当列の最適な幅を計算（最長の owner 名 + 余裕2文字）
        max_owner_len = max([len(t.get("owner", "---")) for t in tasks] + [3])  # 最低3文字（"---"）
        owner_width = min(max_owner_len + 2, 20)  # 最大20文字まで

        table = Table(show_header=True, box=box.SIMPLE, padding=(0, 0))
        table.add_column("ID", style="cyan", width=4)
        table.add_column("状態")
        table.add_column("担当", width=owner_width)
        table.add_column("タスク", style="bold")

        for task in tasks:
            task_id = task.get("id", "")
            status = task.get("status", "pending")
            owner = task.get("owner", "---")
            subject = task.get("subject", "")

            # ステータスアイコン
            if status == "completed":
                status_text = "✓ completed"
                status_style = "green"
            elif status == "in_progress":
                status_text = "● progress"
                status_style = "yellow"
            else:
                status_text = "○ pending"
                status_style = "dim"

            # 依存関係情報
            blocked_info = self.task_store.get_blocked_info(task_id)
            subject_with_block = subject + blocked_info

            table.add_row(
                f"#{task_id}",
                Text(status_text, style=status_style),
                owner,
                subject_with_block
            )

        title = f"◇ タスクリスト ({completed}/{total} 完了)"
        return Panel(table, title=title, border_style="yellow")

    def _make_comms_panel(self) -> Panel:
        """通信ログパネル"""
        messages = self.message_log.get_recent(20)

        table = Table(show_header=False, box=None, padding=(0, 0))
        table.add_column("メッセージ")

        for msg in messages:
            timestamp = msg["timestamp"]
            from_agent = msg["from"]
            to_agent = msg["to"]
            text = msg["text"]
            msg_type = msg["type"]

            # メッセージ種別に応じてスタイル変更
            if msg_type == "task_assignment":
                style = "cyan"
            elif "完了" in text or "✓" in text:
                style = "green"
            elif "待機" in text:
                style = "dim"
            else:
                style = "white"

            # エージェント情報を取得（アイコンと色）
            from_info = self.agents.get(from_agent)
            to_info = self.agents.get(to_agent)

            # アイコン生成：agents辞書にない場合は名前から直接生成
            if from_info:
                from_short = from_info.icon
                from_color = from_info.color
            else:
                from_short = from_agent[:2].upper() if len(from_agent) >= 2 else from_agent[0].upper() if from_agent else "?"
                # 保存された色情報を優先、なければエージェント名からデフォルト色を推測
                saved_from_color = msg.get("from_color")
                if saved_from_color:
                    from_color = AgentInfo.COLOR_MAP.get(saved_from_color, "white")
                else:
                    # エージェント名に基づいてデフォルト色を割り当て
                    color_rotation = ["blue", "green", "yellow", "red", "purple", "cyan", "magenta"]
                    color_index = hash(from_agent) % len(color_rotation)
                    default_color_name = color_rotation[color_index]
                    from_color = AgentInfo.COLOR_MAP.get(default_color_name, "white")

            if to_info:
                to_short = to_info.icon
                to_color = to_info.color
            else:
                to_short = to_agent[:2].upper() if len(to_agent) >= 2 else to_agent[0].upper() if to_agent else "?"
                # 保存された色情報を優先、なければエージェント名からデフォルト色を推測
                saved_to_color = msg.get("to_color")
                if saved_to_color:
                    to_color = AgentInfo.COLOR_MAP.get(saved_to_color, "white")
                else:
                    # エージェント名に基づいてデフォルト色を割り当て
                    color_rotation = ["blue", "green", "yellow", "red", "purple", "cyan", "magenta"]
                    color_index = hash(to_agent) % len(color_rotation)
                    default_color_name = color_rotation[color_index]
                    to_color = AgentInfo.COLOR_MAP.get(default_color_name, "white")

            row = Text()
            row.append(f"{timestamp}  ", style="dim")
            row.append(f"{from_short}", style=f"bold {from_color}")
            row.append("→", style="dim")
            row.append(f"{to_short}  ", style=f"bold {to_color}")
            row.append(text, style=style)

            table.add_row(row)

        count = len(self.message_log.entries)
        title = f"💬 通信ログ ({count}件)"
        return Panel(table, title=title, border_style="magenta")

    def _make_files_panel(self) -> Panel:
        """ファイル変更ログパネル"""
        table = Table(show_header=False, box=None, padding=(0, 0))
        table.add_column("変更")

        # 最新15件
        recent_files = list(self.file_log)[-15:]

        for item in recent_files:
            timestamp = item["timestamp"]
            event_type = item["type"]
            filename = item["file"]

            # イベントタイプのアイコン
            if event_type == "作成":
                icon = "+"
                style = "green"
            elif event_type == "更新":
                icon = "~"
                style = "yellow"
            else:
                icon = "-"
                style = "red"

            row = Text()
            row.append(f"{timestamp}  ", style="dim")
            row.append(f"{icon} ", style=f"bold {style}")
            row.append(f"{event_type:4s}  ", style=style)
            row.append(filename)

            table.add_row(row)

        return Panel(table, title="📁 ファイル変更ログ", border_style="green")

    def _make_footer_panel(self) -> Panel:
        """フッター（メニューバー）パネル - 優しめでおしゃれなデザイン"""
        footer_text = Text()

        # 優しめの背景色
        bg_style = "on grey30"

        # 左側の余白
        footer_text.append("  ", style=bg_style)

        # Qキーメニュー（優しいスカイブルー）
        footer_text.append(" Q ", style="bold white on deep_sky_blue3")
        footer_text.append("  ", style=bg_style)
        footer_text.append("終了", style=f"grey89 {bg_style}")

        # 区切り
        footer_text.append("    │    ", style=f"grey58 {bg_style}")  # 区切り線

        # Ctrl+Cメニュー（優しいオレンジ）
        footer_text.append(" Ctrl+C ", style="bold white on dark_orange")
        footer_text.append("  ", style=bg_style)
        footer_text.append("強制終了", style=f"grey89 {bg_style}")

        # 右側の余白（画面幅いっぱいに背景色を広げる）
        footer_text.append(" " * 80, style=bg_style)

        return Panel(footer_text, border_style="grey58", padding=(0, 0), style=bg_style)


# ─── 待機モード ─────────────────────────────────────────────

def wait_for_team(claude_dir: str, team_name: str, watch_dir: str, interval: int):
    """チームが作成されるまで待機するモード"""
    console = Console()

    # 待機画面の表示
    def make_waiting_panel():
        teams_dir = Path(claude_dir) / "teams"

        # メッセージ作成
        if team_name:
            title = f"🔍 チーム '{team_name}' を待機中"
            message = f"""[bold yellow]チームが見つかりません[/bold yellow]

[dim]監視場所:[/dim] {teams_dir}

[bold green]Claude Code でチームを作成してください[/bold green]

別のターミナルで [cyan]claude[/cyan] を起動して、
[yellow]「{team_name} チームを作って」[/yellow] と指示してください

[dim]チームが作成されると自動的に検出してダッシュボードを開始します[/dim]"""
        else:
            title = "🔍 Agent Teams チームを待機中"
            message = f"""[bold yellow]チームが見つかりません[/bold yellow]

[dim]監視場所:[/dim] {teams_dir}

[bold green]Claude Code でチームを作成してください[/bold green]

別のターミナルで [cyan]claude[/cyan] を起動して、
チームを作成するプロンプトを実行してください

例: [yellow]「フロントエンド担当、バックエンド担当でチームを作って」[/yellow]

[dim]チームが作成されると自動的に検出してダッシュボードを開始します[/dim]"""

        return Panel(
            message,
            title=title,
            border_style="yellow",
            padding=(0, 1)
        )

    # 待機画面の表示とチーム監視
    teams_dir = Path(claude_dir) / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)

    # キーボード監視用フラグ
    quit_flag = threading.Event()
    team_found = threading.Event()
    found_team_name = None

    def check_quit_key():
        """キーボード入力を監視"""
        if HAS_MSVCRT:
            while not quit_flag.is_set() and not team_found.is_set():
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b'q', b'Q'):
                        quit_flag.set()
                        break
                time.sleep(0.1)
        elif HAS_TERMIOS:
            old_settings = termios.tcgetattr(sys.stdin)
            try:
                tty.setcbreak(sys.stdin.fileno())
                while not quit_flag.is_set() and not team_found.is_set():
                    if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                        key = sys.stdin.read(1)
                        if key in ('q', 'Q'):
                            quit_flag.set()
                            break
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    # キーボード監視スレッド開始
    if HAS_MSVCRT or HAS_TERMIOS:
        key_thread = threading.Thread(target=check_quit_key, daemon=True)
        key_thread.start()

    # teams ディレクトリ監視用の簡易ハンドラー
    class TeamCreationWatcher(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                # チームディレクトリが作成された
                dir_name = Path(event.src_path).name
                if not dir_name.startswith('.'):
                    # config.json の存在確認
                    config_path = Path(event.src_path) / "config.json"
                    # 少し待つ（ファイル書き込み完了待ち）
                    time.sleep(0.5)
                    if config_path.exists():
                        nonlocal found_team_name
                        found_team_name = dir_name
                        team_found.set()

        def on_modified(self, event):
            if not event.is_directory and event.src_path.endswith("config.json"):
                # config.json が更新された
                team_dir = Path(event.src_path).parent
                dir_name = team_dir.name
                if not dir_name.startswith('.'):
                    nonlocal found_team_name
                    found_team_name = dir_name
                    team_found.set()

    watcher = TeamCreationWatcher()
    observer = Observer()
    observer.schedule(watcher, str(teams_dir), recursive=True)
    observer.start()

    # チーム検出用の変数
    detected_config = None

    try:
        with Live(make_waiting_panel(), refresh_per_second=2, console=console) as live:
            start_time = time.time()

            while not quit_flag.is_set() and not team_found.is_set():
                elapsed = int(time.time() - start_time)

                # 待機画面を更新（経過時間表示）
                panel = make_waiting_panel()

                # フッター追加
                footer_text = Text()
                bg_style = "on grey30"
                footer_text.append("  ", style=bg_style)
                footer_text.append(" Q ", style="bold white on deep_sky_blue3")
                footer_text.append("  ", style=bg_style)
                footer_text.append("終了", style=f"grey89 {bg_style}")
                footer_text.append("    │    ", style=f"grey58 {bg_style}")
                footer_text.append(f" ⏱ {elapsed}秒経過 ", style=f"grey89 {bg_style}")
                footer_text.append(" " * 60, style=bg_style)

                footer_panel = Panel(footer_text, border_style="grey58", padding=(0, 0), style=bg_style)

                # パネルを縦に並べる（余白なし）
                renderable = Group(panel, footer_panel)

                live.update(renderable)
                time.sleep(0.5)

        # Live コンテキストを抜けてから処理を続行
        if team_found.is_set():
            console.print()
            console.print(f"[bold green]✓ チーム '{found_team_name}' を検出しました！[/bold green]")
            console.print("[dim]ダッシュボードを起動します...[/dim]")
            console.print()

            # 通常モードに移行
            observer.stop()
            observer.join()

            # メイン処理を再実行
            discovery = TeamDiscovery(claude_dir)
            detected_config = discovery.load_team_config(found_team_name)

        elif quit_flag.is_set():
            console.print()
            console.print("[bold yellow]待機を中止しました[/bold yellow]")

    except KeyboardInterrupt:
        console.print()
        console.print("[bold yellow]待機を中止しました[/bold yellow]")
    finally:
        observer.stop()
        observer.join()

    # Live 完全終了後に start_dashboard を呼び出す
    if detected_config:
        start_dashboard(found_team_name, detected_config, TeamDiscovery(claude_dir), claude_dir, watch_dir, interval)


def start_dashboard(team_name: str, config: dict, discovery: TeamDiscovery,
                   claude_dir: str, watch_dir: str, interval: int):
    """ダッシュボードの起動処理（main から分離）"""
    console = Console()

    # 起動メッセージ
    console.print()
    console.print(f"[bold green]Agent Teams ダッシュボード起動[/bold green]")
    console.print(f"チーム: [cyan]{team_name}[/cyan]")
    console.print(f"監視ディレクトリ: [blue]{Path(watch_dir).resolve()}[/blue]")
    console.print()

    # エージェント構築
    agents = discovery.build_agents(config)

    # inboxファイルから過去のエージェントを復元
    agents = discovery.discover_past_agents_from_inboxes(team_name, agents)

    # データストア初期化
    message_log = MessageLog()
    task_store = TaskStore()
    file_log = deque(maxlen=50)

    # Watcher初期化
    config_watcher = ConfigWatcher(agents, discovery, team_name)
    inbox_watcher = InboxWatcher(message_log, agents)
    task_watcher = TaskWatcher(task_store, agents)
    file_watcher = FileChangeWatcher(file_log, agents)

    # Observer設定
    observer = Observer()

    # config.json 監視
    team_dir = Path(claude_dir) / "teams" / team_name
    if team_dir.exists():
        observer.schedule(config_watcher, str(team_dir), recursive=False)

    # Inbox監視
    inbox_dir = Path(claude_dir) / "teams" / team_name / "inboxes"

    # inboxディレクトリが作成されるまで待つ（最大10秒）
    if not inbox_dir.exists():
        for i in range(20):  # 0.5秒 × 20回 = 10秒
            time.sleep(0.5)
            if inbox_dir.exists():
                break

    # 初期読み込み
    if inbox_dir.exists():
        observer.schedule(inbox_watcher, str(inbox_dir), recursive=False)
        inbox_files = [f for f in inbox_dir.glob("*.json") if not f.name.startswith(".")]
        for inbox_file in inbox_files:
            inbox_watcher.on_modified(type('Event', (), {'is_directory': False, 'src_path': str(inbox_file)})())

    # タスク監視
    tasks_dir = Path(claude_dir) / "tasks" / team_name
    if tasks_dir.exists():
        observer.schedule(task_watcher, str(tasks_dir), recursive=False)

    # ファイル監視
    if Path(watch_dir).exists():
        observer.schedule(file_watcher, watch_dir, recursive=True)

    observer.start()

    # ダッシュボード表示
    dashboard = Dashboard(team_name, agents, task_store, message_log, file_log)

    # キーボード監視
    quit_flag = threading.Event()

    def check_quit_key():
        if HAS_MSVCRT:
            while not quit_flag.is_set():
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b'q', b'Q'):
                        quit_flag.set()
                        break
                time.sleep(0.1)
        elif HAS_TERMIOS:
            old_settings = termios.tcgetattr(sys.stdin)
            try:
                tty.setcbreak(sys.stdin.fileno())
                while not quit_flag.is_set():
                    if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                        key = sys.stdin.read(1)
                        if key in ('q', 'Q'):
                            quit_flag.set()
                            break
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    if HAS_MSVCRT or HAS_TERMIOS:
        key_thread = threading.Thread(target=check_quit_key, daemon=True)
        key_thread.start()

    # 少し待機してからダッシュボード表示
    time.sleep(1)

    # ポーリングスレッド: watchdogのイベント取りこぼし対策
    def poll_inboxes_and_tasks():
        """定期的にinbox/taskファイルを再読み込み（watchdogフォールバック）"""
        while not quit_flag.is_set():
            time.sleep(2)  # 2秒間隔でポーリング
            try:
                # inbox再読み込み
                if inbox_dir.exists():
                    for inbox_file in inbox_dir.glob("*.json"):
                        if not inbox_file.name.startswith("."):
                            inbox_watcher.on_modified(
                                type('Event', (), {'is_directory': False, 'src_path': str(inbox_file)})()
                            )
                # task再読み込み
                _tasks_dir = Path(claude_dir) / "tasks" / team_name
                if _tasks_dir.exists():
                    for task_file in _tasks_dir.glob("*.json"):
                        if not task_file.name.startswith("."):
                            task_watcher._handle_task_change(
                                type('Event', (), {'is_directory': False, 'src_path': str(task_file)})()
                            )
            except Exception:
                pass

    poll_thread = threading.Thread(target=poll_inboxes_and_tasks, daemon=True)
    poll_thread.start()

    # メインループ
    try:
        # screen=Trueで代替スクリーンバッファを使用（ちらつき防止）
        with Live(dashboard.render(), refresh_per_second=4, console=console, screen=True) as live:
            while not quit_flag.is_set():
                time.sleep(interval / 1000)
                live.update(dashboard.render())
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


# ─── デモモード ─────────────────────────────────────────────

def run_demo_mode(claude_dir: str, watch_dir: str, interval: int):
    """デモモード: 実際のファイル構造を模擬生成してAgent Teamsをシミュレート"""
    import shutil
    import tempfile

    console = Console()

    # 一時ディレクトリを作成
    temp_claude = Path(tempfile.mkdtemp(prefix="claude_demo_"))
    temp_project = Path(tempfile.mkdtemp(prefix="demo_project_"))

    console.print(f"[dim]一時ディレクトリ: {temp_claude}[/dim]")
    console.print(f"[dim]プロジェクト: {temp_project}[/dim]")
    console.print()

    try:
        # デモチーム作成
        team_name = "demo-team"
        teams_dir = temp_claude / "teams" / team_name
        tasks_dir = temp_claude / "tasks" / team_name
        inboxes_dir = teams_dir / "inboxes"

        teams_dir.mkdir(parents=True)
        tasks_dir.mkdir(parents=True)
        inboxes_dir.mkdir(parents=True)

        # config.json 作成
        config = {
            "name": team_name,
            "description": "デモチーム - 自己紹介Webページ作成",
            "createdAt": int(datetime.now().timestamp() * 1000),
            "leadAgentId": f"team-lead@{team_name}",
            "leadSessionId": "demo-session-001",
            "members": [
                {
                    "agentId": f"team-lead@{team_name}",
                    "name": "team-lead",
                    "agentType": "team-lead",
                    "model": "claude-opus-4-6",
                    "color": "yellow",
                    "joinedAt": int(datetime.now().timestamp() * 1000),
                    "tmuxPaneId": "in-process",
                    "cwd": str(temp_project),
                    "subscriptions": []
                },
                {
                    "agentId": f"designer@{team_name}",
                    "name": "designer",
                    "agentType": "general-purpose",
                    "model": "sonnet",
                    "prompt": "UI/UXデザイン担当",
                    "color": "blue",
                    "planModeRequired": False,
                    "joinedAt": int(datetime.now().timestamp() * 1000),
                    "tmuxPaneId": "in-process",
                    "cwd": str(temp_project),
                    "subscriptions": [],
                    "backendType": "in-process"
                },
                {
                    "agentId": f"frontend@{team_name}",
                    "name": "frontend",
                    "agentType": "general-purpose",
                    "model": "sonnet",
                    "prompt": "フロントエンド実装担当",
                    "color": "green",
                    "planModeRequired": False,
                    "joinedAt": int(datetime.now().timestamp() * 1000),
                    "tmuxPaneId": "in-process",
                    "cwd": str(temp_project),
                    "subscriptions": [],
                    "backendType": "in-process"
                },
                {
                    "agentId": f"qa@{team_name}",
                    "name": "qa",
                    "agentType": "general-purpose",
                    "model": "sonnet",
                    "prompt": "品質保証・セキュリティ担当",
                    "color": "purple",
                    "planModeRequired": False,
                    "joinedAt": int(datetime.now().timestamp() * 1000),
                    "tmuxPaneId": "in-process",
                    "cwd": str(temp_project),
                    "subscriptions": [],
                    "backendType": "in-process"
                }
            ]
        }

        with open(teams_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        # Inbox ファイル作成（空配列で初期化）
        for agent in ["team-lead", "designer", "frontend", "qa"]:
            with open(inboxes_dir / f"{agent}.json", "w", encoding="utf-8") as f:
                json.dump([], f)

        # タスクファイル作成
        tasks = [
            {
                "id": "1",
                "subject": "デザイン設計（レイアウト・配色・アクセシビリティ）",
                "description": "自己紹介Webページのデザイン設計",
                "activeForm": "デザイン設計中",
                "owner": "",
                "status": "pending",
                "blocks": ["2"],
                "blockedBy": []
            },
            {
                "id": "2",
                "subject": "フロントエンド実装（HTML/CSS/JS）",
                "description": "HTML/CSS/JSの実装",
                "activeForm": "フロントエンド実装中",
                "owner": "",
                "status": "pending",
                "blocks": ["3"],
                "blockedBy": ["1"]
            },
            {
                "id": "3",
                "subject": "品質保証・セキュリティ検証",
                "description": "QA検証とセキュリティチェック",
                "activeForm": "QA検証中",
                "owner": "",
                "status": "pending",
                "blocks": [],
                "blockedBy": ["2"]
            }
        ]

        for task in tasks:
            with open(tasks_dir / f"{task['id']}.json", "w", encoding="utf-8") as f:
                json.dump(task, f, indent=2, ensure_ascii=False)

        console.print("[green]✓ デモ環境を構築しました[/green]")
        console.print("[yellow]デモシナリオを実行します（約30秒）...[/yellow]")
        console.print()

        # デモシナリオを実行
        def demo_scenario():
            """デモシナリオ: タスク更新とメッセージ送信をシミュレート"""
            time.sleep(2)

            # ステップ1: デザイナーがタスク1を開始
            console.print("[cyan]➤ designer がタスク#1を開始[/cyan]")
            tasks[0]["owner"] = "designer"
            tasks[0]["status"] = "in_progress"
            with open(tasks_dir / "1.json", "w", encoding="utf-8") as f:
                json.dump(tasks[0], f, indent=2, ensure_ascii=False)

            # メッセージ送信
            with open(inboxes_dir / "team-lead.json", "r+", encoding="utf-8") as f:
                messages = json.load(f)
                messages.append({
                    "from": "designer",
                    "text": "タスク#1のデザイン設計を開始します",
                    "summary": "デザイン設計開始",
                    "timestamp": datetime.now().isoformat() + "Z",
                    "color": "blue",
                    "read": False
                })
                f.seek(0)
                json.dump(messages, f, indent=2, ensure_ascii=False)

            time.sleep(5)

            # ファイル作成
            (temp_project / "design-spec.md").write_text("# Design Spec\n\nDemo design...", encoding="utf-8")

            time.sleep(3)

            # ステップ2: デザイナーがタスク1を完了
            console.print("[green]➤ designer がタスク#1を完了[/green]")
            tasks[0]["status"] = "completed"
            with open(tasks_dir / "1.json", "w", encoding="utf-8") as f:
                json.dump(tasks[0], f, indent=2, ensure_ascii=False)

            with open(inboxes_dir / "team-lead.json", "r+", encoding="utf-8") as f:
                messages = json.load(f)
                messages.append({
                    "from": "designer",
                    "text": '{"type":"task_completed","taskId":"1","taskSubject":"デザイン設計"}',
                    "summary": "Design spec completed, task #1 done",
                    "timestamp": datetime.now().isoformat() + "Z",
                    "color": "blue",
                    "read": False
                })
                f.seek(0)
                f.truncate()
                json.dump(messages, f, indent=2, ensure_ascii=False)

            time.sleep(3)

            # ステップ3: フロントエンドがタスク2を開始
            console.print("[cyan]➤ frontend がタスク#2を開始[/cyan]")
            tasks[1]["owner"] = "frontend"
            tasks[1]["status"] = "in_progress"
            with open(tasks_dir / "2.json", "w", encoding="utf-8") as f:
                json.dump(tasks[1], f, indent=2, ensure_ascii=False)

            with open(inboxes_dir / "team-lead.json", "r+", encoding="utf-8") as f:
                messages = json.load(f)
                messages.append({
                    "from": "frontend",
                    "text": "タスク#2の実装を開始します",
                    "summary": "実装開始",
                    "timestamp": datetime.now().isoformat() + "Z",
                    "color": "green",
                    "read": False
                })
                f.seek(0)
                f.truncate()
                json.dump(messages, f, indent=2, ensure_ascii=False)

            time.sleep(4)

            # ファイル作成
            (temp_project / "index.html").write_text("<html>...</html>", encoding="utf-8")
            time.sleep(2)
            (temp_project / "style.css").write_text("body { ... }", encoding="utf-8")
            time.sleep(2)
            (temp_project / "script.js").write_text("console.log('demo');", encoding="utf-8")

            time.sleep(3)

            # ステップ4: フロントエンドがタスク2を完了
            console.print("[green]➤ frontend がタスク#2を完了[/green]")
            tasks[1]["status"] = "completed"
            with open(tasks_dir / "2.json", "w", encoding="utf-8") as f:
                json.dump(tasks[1], f, indent=2, ensure_ascii=False)

            with open(inboxes_dir / "team-lead.json", "r+", encoding="utf-8") as f:
                messages = json.load(f)
                messages.append({
                    "from": "frontend",
                    "text": "フロントエンド実装完了 (HTML/CSS/JS 3ファイル)",
                    "summary": "実装完了",
                    "timestamp": datetime.now().isoformat() + "Z",
                    "color": "green",
                    "read": False
                })
                f.seek(0)
                f.truncate()
                json.dump(messages, f, indent=2, ensure_ascii=False)

            time.sleep(3)

            # ステップ5: QAがタスク3を開始・完了
            console.print("[cyan]➤ qa がタスク#3を開始[/cyan]")
            tasks[2]["owner"] = "qa"
            tasks[2]["status"] = "in_progress"
            with open(tasks_dir / "3.json", "w", encoding="utf-8") as f:
                json.dump(tasks[2], f, indent=2, ensure_ascii=False)

            time.sleep(4)

            (temp_project / "qa-report.md").write_text("# QA Report\n\nAll tests passed.", encoding="utf-8")

            time.sleep(2)

            console.print("[green]➤ qa がタスク#3を完了[/green]")
            tasks[2]["status"] = "completed"
            with open(tasks_dir / "3.json", "w", encoding="utf-8") as f:
                json.dump(tasks[2], f, indent=2, ensure_ascii=False)

            with open(inboxes_dir / "team-lead.json", "r+", encoding="utf-8") as f:
                messages = json.load(f)
                messages.append({
                    "from": "qa",
                    "text": "QA完了、脆弱性なし",
                    "summary": "QA完了",
                    "timestamp": datetime.now().isoformat() + "Z",
                    "color": "purple",
                    "read": False
                })
                f.seek(0)
                f.truncate()
                json.dump(messages, f, indent=2, ensure_ascii=False)

            console.print()
            console.print("[bold green]✓ デモシナリオ完了！[/bold green]")
            console.print("[yellow]ダッシュボードを起動します...[/yellow]")
            time.sleep(2)

        # シナリオスレッド起動
        scenario_thread = threading.Thread(target=demo_scenario, daemon=True)
        scenario_thread.start()

        # ダッシュボードを起動（一時ディレクトリを使用）
        discovery = TeamDiscovery(str(temp_claude))
        result = discovery.find_active_team()

        if not result:
            console.print("[red]Error: デモチームの検出に失敗[/red]")
            return

        team_name, config = result
        agents = discovery.build_agents(config)

        message_log = MessageLog()
        task_store = TaskStore()
        file_log = deque(maxlen=50)

        inbox_watcher = InboxWatcher(message_log, agents)
        task_watcher = TaskWatcher(task_store, agents)
        file_watcher = FileChangeWatcher(file_log, agents)

        observer = Observer()
        observer.schedule(inbox_watcher, str(inboxes_dir), recursive=False)
        observer.schedule(task_watcher, str(tasks_dir), recursive=False)
        observer.schedule(file_watcher, str(temp_project), recursive=True)
        observer.start()

        dashboard = Dashboard(team_name, agents, task_store, message_log, file_log)

        try:
            # screen=Trueで代替スクリーンバッファを使用（ちらつき防止）
            with Live(dashboard.render(), refresh_per_second=4, console=console, screen=True) as live:
                while scenario_thread.is_alive():
                    time.sleep(interval / 1000)
                    live.update(dashboard.render())

                # シナリオ完了後も10秒間表示
                console.print()
                console.print("[dim]10秒後に終了します...[/dim]")
                for _ in range(10):
                    time.sleep(1)
                    live.update(dashboard.render())

        except KeyboardInterrupt:
            pass
        finally:
            observer.stop()
            observer.join()

    finally:
        # クリーンアップ
        shutil.rmtree(temp_claude, ignore_errors=True)
        shutil.rmtree(temp_project, ignore_errors=True)
        console.print()
        console.print("[bold yellow]デモモードを終了しました[/bold yellow]")


# ─── メイン処理 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent Teams ダッシュボード V1.0")
    parser.add_argument("--watch-dir", default=".", help="監視するプロジェクトディレクトリ")
    parser.add_argument("--claude-dir", default=str(Path.home() / ".claude"), help="Claude ディレクトリ")
    parser.add_argument("--team", help="チーム名を明示指定")
    parser.add_argument("--demo", action="store_true", help="デモモード")
    parser.add_argument("--interval", type=int, default=250, help="更新間隔(ms)")

    args = parser.parse_args()

    # デモモード
    if args.demo:
        console = Console()
        console.print("[bold green]デモモードを起動します...[/bold green]")
        run_demo_mode(args.claude_dir, args.watch_dir, args.interval)
        return

    # チーム検出
    discovery = TeamDiscovery(args.claude_dir)

    if args.team:
        team_name = args.team
        config = discovery.load_team_config(team_name)
        if not config:
            # 待機モードに移行
            wait_for_team(args.claude_dir, team_name, args.watch_dir, args.interval)
            return
    else:
        result = discovery.find_active_team()
        if not result:
            # 待機モードに移行
            wait_for_team(args.claude_dir, None, args.watch_dir, args.interval)
            return
        team_name, config = result

    # ダッシュボードを起動
    start_dashboard(team_name, config, discovery, args.claude_dir, args.watch_dir, args.interval)


if __name__ == "__main__":
    main()
