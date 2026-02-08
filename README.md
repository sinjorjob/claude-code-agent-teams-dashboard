# Claude Code Agent Teams Dashboard

Claude Code Agent Teams のリアルタイムダッシュボード（ターミナル版）

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-beta-orange.svg)

## ⚠️ ベータ版について

**このツールは 2026年2月9日時点の Claude Code Agent Teams の仕様に基づいて作成されています。**

Agent Teams は実験的機能であり、将来のアップデートで仕様が変更される可能性があります。その場合、このダッシュボードが正常に動作しなくなる可能性があります。

## 概要

Claude Code Agent Teams で作成したチームの状態を、ターミナル上でリアルタイムに可視化するダッシュボードです。

**主な機能**:
- ✅ エージェントの状態表示（稼働中/完了/待機中）
- ✅ タスクリストと依存関係の可視化
- ✅ エージェント間の通信ログ
- ✅ ファイル変更のリアルタイム監視
- ✅ チーム自動検出と待機モード
- ✅ デモモード搭載

## スクリーンショット

```
┌────────────────────────────────────────────────────────────────┐
│  🤖 Claude Code Agent Teams Dashboard  16:58:43               │
├────────────────────────────────────────────────────────────────┤
│  AGENTS (4)                      │  TASKS (3)                  │
│  ┌──────────────────────────┐   │  ┌─────────────────────┐   │
│  │ team-lead                │   │  │ #1 [completed]      │   │
│  │   Status: idle           │   │  │   デザイン設計       │   │
│  │   Model: Opus 4.6        │   │  │   Owner: designer   │   │
│  ├──────────────────────────┤   │  ├─────────────────────┤   │
│  │ designer                 │   │  │ #2 [in_progress]    │   │
│  │   Status: working        │   │  │   実装              │   │
│  │   Model: Sonnet          │   │  │   Owner: frontend   │   │
│  │   Task: #1 デザイン設計   │   │  │   ← blocked by #1   │   │
│  └──────────────────────────┘   │  └─────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│  COMMUNICATIONS (last 10)                                      │
│  [16:58:20] team-lead → designer  "Start task #1..."          │
│  [16:58:35] designer → team-lead  "Task #1 completed"         │
├──────────────────────────────────────────────────────────────┤
│  FILE ACTIVITY (last 8)                                        │
│  [16:58:22] Created: demo/design-spec.md                      │
└──────────────────────────────────────────────────────────────┘
```

## インストール

### 前提条件

- Python 3.8 以上
- Claude Code がインストールされていること
- Agent Teams が有効化されていること（`settings.local.json` に `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）

### セットアップ

```bash
# リポジトリをクローン
git clone https://github.com/sinjorjob/claude-code-agent-teams-dashboard.git
cd claude-code-agent-teams-dashboard

# 仮想環境を作成
python -m venv venv

# 依存ライブラリをインストール
# Windows
venv\Scripts\pip install -r requirements.txt

# Mac/Linux
source venv/bin/activate
pip install -r requirements.txt
```

## 使い方

### 基本的な使い方

**1. ダッシュボードを起動**

```bash
# Windows
venv\Scripts\python agent_dashboard.py

# Mac/Linux
python agent_dashboard.py
```

チームが存在しない場合、待機画面が表示されます。

**2. 別のターミナルで Claude Code を起動してチームを作成**

```bash
claude
```

起動後、以下のようにプロンプトを実行：

```
フロントエンド担当、バックエンド担当でチームを作って
```

**3. 自動検出**

チームが作成されると、ダッシュボードが自動的に検出してリアルタイム監視を開始します。

### デモモード

実際のファイル構造を生成してデモシナリオを実行します。

```bash
# Windows
venv\Scripts\python agent_dashboard.py --demo

# Mac/Linux
python agent_dashboard.py --demo
```

約30秒で完了し、Agent Teams の動作を体感できます。

### オプション

```bash
# 特定のチーム名を指定
python agent_dashboard.py --team my-team

# 監視ディレクトリを指定
python agent_dashboard.py --watch-dir /path/to/project

# Claude ディレクトリを指定（デフォルト: ~/.claude）
python agent_dashboard.py --claude-dir /custom/path/.claude

# 更新間隔を変更（デフォルト: 250ms）
python agent_dashboard.py --interval 500
```

### キーボード操作

- **Q**: ダッシュボードを終了
- **Ctrl+C**: 強制終了

## 仕組み

このダッシュボードは、Claude Code Agent Teams が使用するローカルファイルをリアルタイムで監視します。

**監視対象**:
- `~/.claude/teams/{team-name}/config.json` - チーム構成
- `~/.claude/teams/{team-name}/inboxes/` - エージェント間メッセージ
- `~/.claude/tasks/{team-name}/` - タスク情報
- プロジェクトディレクトリ - ファイル変更

**主要機能**:
- **チーム自動検出**: `~/.claude/teams/` から最新チームを自動検出
- **動的エージェント構築**: `config.json` から動的にメンバー情報を読み込み
- **タスク依存関係表示**: `blockedBy` を視覚的に表示
- **構造化メッセージ解析**: `task_assignment`, `idle_notification` 等を自動認識
- **差分メッセージ抽出**: inbox の新規メッセージのみを効率的に抽出

## 制限事項

### Agent Teams の仕様による制限

- **セッション終了後のデータ削除**: チームは実行中のみ監視可能。セッション終了後は `teams/` と `tasks/` ディレクトリが削除されます
- **リアルタイム監視専用**: 過去のチーム情報は閲覧できません
- **Windows 環境**: In-Process モードのみサポート（split-pane モードは非対応）

### ダッシュボードの制限

- **読み取り専用**: Agent Teams の状態ファイルを書き換えません
- **エージェントの詳細**: 現在実行中のツールなどの詳細情報は inbox メッセージから推測します

## トラブルシューティング

### チームが検出されない

**確認事項**:
1. `settings.local.json` に `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` が設定されているか
2. Claude Code を再起動したか
3. チームを作成するプロンプトに「チームを作って」という明示的な指示があるか

### ダッシュボードが更新されない

**対処法**:
```bash
# watchdog が正しくインストールされているか確認
pip install watchdog
```

### 色が正しく表示されない

**対処法**:
- Windows Terminal や ConEmu などの最新ターミナルを使用してください
- 古い cmd.exe では ANSI カラーがサポートされていません

## ライセンス

MIT License

## 作者

[@sinjorjob](https://github.com/sinjorjob)

---

**Note**: このツールは非公式プロジェクトであり、Anthropic とは関係ありません。
