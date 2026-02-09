---
title: "Claude Code Agent Teams完全ガイド：複数AIエージェントによるチーム開発の全て"
emoji: "🤖"
type: "tech"
topics: ["claudecode", "ai", "開発効率化", "agentteams", "anthropic"]
published: false
---

# はじめに

Claude Code に **Agent Teams** という革新的な機能が実験的に追加されました。これは、複数の Claude AI エージェントが**チームとして協力**してタスクを実行する機能です。

本記事では、Agent Teams の仕組みから実践的な使い方、内部アーキテクチャまで、実際にプロジェクトで運用して得た知見を元に**超詳細に解説**します。

## この記事で分かること

- ✅ Agent Teams の基本概念と従来の Subagent との違い
- ✅ 実際のファイル構造とデータフォーマット（config.json, tasks, inboxes）
- ✅ Windows 環境でのセットアップと動作確認手順
- ✅ リアルタイムダッシュボードの実装方法
- ✅ 実務で使えるチーム構成パターンとユースケース
- ✅ ベストプラクティス

:::message
**対象読者**: Claude Code を使った開発経験がある方、AI エージェントによる自動化に興味がある方
:::

---

# Agent Teams とは？

## 基本概念

**Claude Code Agent Teams** は、複数の Claude AI エージェントが独立したコンテキストを持ちながら、メールボックス方式でメッセージをやり取りして協力するシステムです。

```
通常の Claude Code:   1人のエージェントが全て担当
Agent Teams:          チームリーダー + 複数のメンバーが分担して同時進行
```

### アーキテクチャの違い

従来の Subagent と Agent Teams の比較：

| 比較項目 | Subagent（Task tool） | Agent Teams |
|---------|----------------------|-------------|
| **構造** | 親→子の一方向 | チームメンバー間で直接通信 |
| **コンテキスト** | 親エージェントと共有 | 各メンバーが独立したコンテキストウィンドウ |
| **メッセージング** | 結果を返すだけ | メールボックス（inbox）で双方向やりとり |
| **適した作業** | 単発の調査や分析 | 並行して進める複数タスク、複雑なワークフロー |
| **タスク管理** | 親が管理 | 共有タスクリスト（依存関係あり） |

---

# セットアップ方法

## 前提条件

- Claude Code がインストールされていること
- Windows 環境（本記事では Windows を前提に解説）
- Python 3.8 以上（ダッシュボード実装に必要）

## ステップ1: 実験的機能の有効化

プロジェクトディレクトリで Claude Code を起動し、以下のプロンプトを実行：

```
./.claude/settings.local.json に以下のパラメーターを追加して（.claude ディレクトリとファイルが存在しない場合は作成してから）
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

:::message alert
**重要**: `settings.local.json` は**プロジェクト直下**の `./.claude/` ディレクトリに配置してください。これにより、このプロジェクトでのみ Agent Teams が有効になります。
:::

## ステップ2: Claude Code の再起動

設定を反映させるため、必ず再起動してください。

```powershell
# ターミナルを閉じて、もう一度起動
claude
```

## ステップ3: チームの作成

Claude Code に以下のようにプロンプトを入力するだけ：

```
フロントエンド担当、バックエンド担当でチームを作って
```

これだけで、チームメンバーが自動的に生成されます！

---

# 内部アーキテクチャの詳細

## ファイルストレージ構成

Agent Teams は、すべての状態をローカルファイルとして保存します。

```
~/.claude/
├── teams/{team-name}/
│   ├── config.json          ← チーム構成（メンバー一覧）
│   └── inboxes/
│       ├── team-lead.json   ← リーダーの受信メッセージ
│       ├── member-1.json    ← メンバー1の受信メッセージ
│       └── member-2.json    ← メンバー2の受信メッセージ
├── tasks/{team-name}/
│   ├── 1.json               ← タスク#1
│   ├── 2.json               ← タスク#2
│   └── ...
└── projects/{project-hash}/
    └── {session-id}.jsonl   ← セッションログ
```

:::message
**Windows パス**: `C:\Users\<ユーザー名>\.claude\teams\...`
:::

### 重要な仕様：データ保存場所は固定

Agent Teams のデータは **必ずホームディレクトリの `~/.claude/` に保存されます**。これは Claude Code の設計仕様であり、変更できません。

- ✅ **常に保存される場所**: `~/.claude/teams/`, `~/.claude/tasks/`
- ❌ **保存されない場所**: `./.claude/teams/` (プロジェクト直下)
- ❌ **設定で変更**: 不可能

**設計哲学**: すべてのローカルデータをホームディレクトリに集約することで、予測可能で、検査・デバッグ・クリーンアップが容易になります。

## データ形式の詳細

### config.json（チーム構成）

実際の config.json の例：

```json
{
  "name": "self-intro-web",
  "description": "自己紹介Webページ作成チーム",
  "createdAt": 1770537462583,
  "leadAgentId": "team-lead@self-intro-web",
  "leadSessionId": "7aa2e392-4a72-4248-857b-4e0dcabc34dd",
  "members": [
    {
      "agentId": "team-lead@self-intro-web",
      "name": "team-lead",
      "agentType": "team-lead",
      "model": "claude-opus-4-6",
      "joinedAt": 1770537462583,
      "tmuxPaneId": "",
      "cwd": "C:\\develop\\claude-code\\agent-team\\demo",
      "subscriptions": []
    },
    {
      "agentId": "designer@self-intro-web",
      "name": "designer",
      "agentType": "general-purpose",
      "model": "sonnet",
      "prompt": "あなたはUI/UXデザイナーです...",
      "color": "blue",
      "planModeRequired": false,
      "joinedAt": 1770537494440,
      "tmuxPaneId": "in-process",
      "cwd": "C:\\develop\\claude-code\\agent-team\\demo",
      "subscriptions": [],
      "backendType": "in-process"
    }
  ]
}
```

**重要なフィールド**:
- `leadSessionId`: リーダーのセッションID（復元時の識別子）
- `tmuxPaneId`: Windows 環境では "in-process"
- `color`: UI 表示用の色（"blue", "green", "yellow" 等）
- `backendType`: "in-process"（全メンバー共通）

### タスクファイル（{N}.json）

タスクの実際のデータ構造：

```json
{
  "id": "1",
  "subject": "デザイン設計（レイアウト・配色・アクセシビリティ）",
  "description": "自己紹介Webページのデザイン設計を行う...",
  "activeForm": "デザイン設計中",
  "owner": "designer",
  "status": "completed",
  "blocks": ["2"],
  "blockedBy": []
}
```

**タスクステータスの遷移**:
- `pending` → `in_progress` → `completed`

**依存関係の管理**:
- `blocks`: このタスクが完了するまでブロックされるタスクのID配列
- `blockedBy`: このタスクをブロックしているタスクのID配列

### Inbox メッセージ（{agent}.json）

メッセージの実際の形式：

```json
{
  "from": "designer",
  "text": "デザイン仕様書を作成完了しました。\n\nファイル: ...",
  "summary": "Design spec completed, task #1 done",
  "timestamp": "2026-02-08T07:59:48.913Z",
  "color": "blue",
  "read": true
}
```

**構造化メッセージの例**（task_assignment）:

```json
{
  "from": "designer",
  "text": "{\"type\":\"task_assignment\",\"taskId\":\"1\",\"subject\":\"...\"}",
  "timestamp": "2026-02-08T07:58:20.294Z",
  "color": "blue",
  "read": false
}
```

**主なメッセージタイプ**:
- `task_assignment`: タスク割り当て
- `idle_notification`: アイドル状態通知（タスク完了後）
- `task_completed`: タスク完了通知
- `shutdown_request` / `shutdown_approved`: 終了リクエスト

---

# チュートリアル：3人チームでWebページを作る

シンプルな自己紹介Webページを **3人チーム** で作りながら、Agent Teams の基本的な使い方を学びます。

### 手順

**1. 作業フォルダを作成**

```powershell
mkdir my-profile-page
cd my-profile-page
claude
```

**2. チームを作成（プロンプト）**

```
以下の3人チームを作って、自己紹介Webページを作成してください。

- UI/UXデザイナー担当: デザイン設計（レイアウト、配色、アクセシビリティ考慮）
- フロントエンド実装担当: HTML/CSS/JSの実装（レスポンシブ対応、アニメーション）
- 品質保証・セキュリティ担当: コードレビュー、XSS脆弱性チェック、アクセシビリティ検証

完成したら統合テストを実施してください。
```

**3. チームの動きを観察**

`Shift + ↑/↓` でメンバーを切り替えて、各エージェントの作業状況を確認できます。

- チームリーダーがタスクを分配
- UI/UXデザイナーがデザイン仕様を作成
- フロントエンド実装担当がコーディング
- 品質保証担当がセキュリティレビュー
- メールボックスでやりとり（「このクラス名で統一します」等）
- 最終的にリーダーが統合テスト・承認

**4. 結果を確認**

```powershell
# ブラウザで開く（Windows）
start index.html
```

### 期待される成果物

```
my-profile-page/
├── DESIGN.md        ← UI/UXデザイナーが作成（デザイン仕様書）
├── index.html       ← フロントエンド実装担当が作成
├── style.css        ← フロントエンド実装担当が作成
├── script.js        ← フロントエンド実装担当が作成
└── REVIEW.md        ← 品質保証担当が作成（レビュー結果）
```

---

# ワークフロー実例

## タスクフロー

実際のチーム作業では、以下のようなタスクフローが実行されます：

```
タスク1（デザイン設計）→ タスク2（実装）→ タスク3（QA検証）
  owner: designer          owner: frontend      owner: qa
  blocks: [2]              blocks: [3]          blocks: []
  blockedBy: []            blockedBy: [1]       blockedBy: [2]
```

## メッセージ交換の流れ

チーム作成から完了まで、以下のような流れで処理が進みます：

### 1. チーム作成 → タスク分配

```
user → team-lead: "3人チームを作って..."
team-lead: チーム構成を決定、config.json 作成
team-lead: designer, frontend, qa-engineer を spawn
team-lead → 各メンバー: タスク割り当て（inbox経由）
```

### 2. デザイン → 実装 → QA

```
designer: design-spec.md を作成 → タスク#1 完了 → リーダーに報告
  ↓
frontend: デザイン仕様を読み込み → index.html, style.css, script.js 作成 → タスク#2 完了
  ↓
qa-engineer: コード検証 → XSS対策・アクセシビリティ改善 → qa-report.md 作成 → タスク#3 完了
  ↓
team-lead: 全タスク完了を確認 → 統合テスト → 終了
```

各フェーズの完了時に `idle_notification` メッセージが送信され、リーダーが次のタスクの開始を指示します。

---

# Windows環境でのエージェント可視化

## パターン1: Windows Terminalの分割ペイン

Windows Terminalの分割機能で各エージェントを並べて表示できます。

### キー操作

| 操作 | キー |
|------|------|
| 右に分割 | `Alt + Shift + +`（プラスキー） |
| 下に分割 | `Alt + Shift + -`（マイナスキー） |
| ペイン間移動 | `Alt + ↑/↓/←/→` |
| ペインサイズ変更 | `Alt + Shift + ↑/↓/←/→` |

### 3分割のレイアウト例

```
┌─────────────────────┬─────────────────────┐
│                     │                     │
│   チームリーダー      │   HTMLデザイナー      │
│   (claude)          │   (メンバー表示)      │
│                     │                     │
├─────────────────────┴─────────────────────┤
│                                           │
│   CSSスタイリスト / JSエンジニア              │
│   (メンバー表示)                             │
│                                           │
└───────────────────────────────────────────┘
```

## パターン2: ターミナル版ダッシュボード（おすすめ）

Rich ライブラリを使った本格的なターミナルベースのリアルタイムダッシュボード。

:::message
**実際に作成したツールをGitHubで公開中！**
https://github.com/sinjorjob/claude-code-agent-teams-dashboard

この記事を書く過程で、Agent Teams の内部仕組みを理解するために実際にダッシュボードツールを開発しました。
:::

![Dashboard Screenshot](https://raw.githubusercontent.com/sinjorjob/claude-code-agent-teams-dashboard/main/images/dashboard.png)

### ダッシュボードツールの特徴

このツールは、Agent Teams が使用するローカルファイル（`~/.claude/teams/`, `~/.claude/tasks/`）をリアルタイムで監視し、以下の情報を可視化します：

**主要機能**:

| 機能 | 説明 |
|------|------|
| 🔍 **チーム自動検出** | チームが存在しない場合は待機画面を表示し、チーム作成を自動検出 |
| 👥 **エージェント状態** | 各メンバーのステータス（稼働中/完了/待機中）、使用モデル、現在のタスクを表示 |
| 📋 **タスク依存関係** | `blockedBy` を視覚的に表示（例: `← blocked by #1`） |
| 💬 **通信ログ** | エージェント間のメッセージを時系列で表示（構造化メッセージの自動認識） |
| 📁 **ファイル変更監視** | プロジェクト内のファイル作成・編集をリアルタイム表示 |
| 🎮 **デモモード** | 実際のファイル構造を生成して動作確認が可能 |

### インストールと起動

**1. リポジトリをクローン**

```bash
git clone https://github.com/sinjorjob/claude-code-agent-teams-dashboard.git
cd claude-code-agent-teams-dashboard

# 仮想環境の作成
python -m venv venv

# 依存ライブラリのインストール（Windows）
venv\Scripts\pip install -r requirements.txt

# 依存ライブラリのインストール（Mac/Linux）
source venv/bin/activate
pip install -r requirements.txt
```

**2. ダッシュボードを起動**

```powershell
# Windows
venv\Scripts\python agent_dashboard.py

# Mac/Linux
python agent_dashboard.py
```

**3. チーム作成（別のターミナルで）**

```bash
claude
```

起動後、以下のようにプロンプトを実行：

```
フロントエンド担当、バックエンド担当でチームを作って
```

チームが作成されると、ダッシュボードが自動的に検出してリアルタイム監視を開始します。

### デモモード

実際のチームを作成せずに、ダッシュボードの動作を確認できます：

```powershell
# Windows
venv\Scripts\python agent_dashboard.py --demo

# Mac/Linux
python agent_dashboard.py --demo
```

約30秒で自己紹介Webページを作成する3人チーム（デザイナー、実装、QA）のシナリオが実行され、Agent Teams の動作を体感できます。

### ダッシュボードの仕組み

このツールは、watchdog ライブラリでファイルシステムを監視し、以下のファイルから情報を取得します：

```
~/.claude/
├── teams/{team-name}/
│   ├── config.json          # チーム構成（メンバー一覧、モデル、色）
│   └── inboxes/             # エージェント間メッセージ
│       ├── team-lead.json
│       ├── member-1.json
│       └── member-2.json
├── tasks/{team-name}/
│   ├── 1.json               # タスク情報（status, owner, blockedBy）
│   ├── 2.json
│   └── ...
```

**技術的なポイント**:

1. **差分メッセージ抽出**: inbox ファイルの読み込み位置を記録し、新規メッセージのみを抽出
2. **動的エージェント構築**: `config.json` からメンバー情報を動的に読み込み
3. **過去エージェント復元**: セッション終了後も inbox ファイルから過去のエージェント情報を復元
4. **構造化メッセージ解析**: `task_assignment`, `idle_notification` などのメッセージタイプを自動認識

### 開発の経緯

この記事を書く過程で、Agent Teams の内部動作を理解するために：

1. **実際にチームを作成**して `~/.claude/` 配下のファイル構造を調査
2. **config.json, tasks/*.json, inboxes/*.json** の実際のデータ形式を解析
3. **リアルタイム監視ツール**を実装して動作を可視化
4. **デモモード**を追加して誰でも試せるように改良

このツールを使うことで、Agent Teams がどのように動作しているかを視覚的に理解できます。

---

# データライフサイクルと制限事項

## セッション終了後のデータ削除

Agent Teams は **セッション終了後にチーム定義が自動削除される** 仕様です。

| データ | 実行中 | セッション終了後 | /resume 後 |
|--------|--------|-----------------|-----------|
| **teams/{team-name}/config.json** | ✅ 存在 | ❌ 削除 | ❌ 復元不可 |
| **teammates（エージェント）** | ✅ 実行中 | ❌ 終了 | ❌ 復元不可 |
| **teams/{team-name}/inboxes/** | ✅ 存在 | ❌ 削除 | ❌ 復元不可 |
| **tasks/{team-name}/** タスクデータ | ✅ 存在 | ❌ 削除 | ❌ 復元不可 |
| **projects/{hash}/{session-id}.jsonl** | ✅ 記録中 | ✅ 保持 | ✅ 保持 |

:::message alert
**重要**: `/resume` や `/rewind` コマンドでは teammates は復元されません。新しい teammates を手動で spawn し直す必要があります。
:::

## チーム構成の再利用方法

❌ **明示的なチームテンプレート機能は存在しない**
- YAML や設定ファイルでチームを事前定義する機能はない
- `config.json` は実行時に自動生成され、セッション終了後に削除される

✅ **自然言語プロンプトで再利用可能**

### 方法1: プロンプトテンプレートファイルを作成

```markdown
# team-templates/ui-development-team.md
以下の3人チームを作って：

- UI/UXデザイナー担当: デザイン設計
- フロントエンド実装担当: HTML/CSS/JS実装
- 品質保証・セキュリティ担当: コードレビュー、検証
```

使用時: プロンプトをコピペして実行

### 方法2: CLAUDE.md に記載

```markdown
# CLAUDE.md

## チーム構成テンプレート

プロジェクトでチームが必要な場合、以下の構成を使用：

- UI/UXデザイナー (ui-designer): デザイン設計担当
- フロントエンド実装 (frontend-dev): HTML/CSS/JS実装担当
- 品質保証・セキュリティ (qa-security): レビュー・検証担当
```

すべての teammates が自動的に `CLAUDE.md` を読み込むため、プロジェクト固有のチーム構成を定義できます。

---

# コスト感覚とパフォーマンス

## 料金目安

| チーム構成 | 入力トークン | 出力トークン | 概算コスト |
|-----------|------------|------------|-----------|
| 3人チーム（Sonnet） | 50-70K | 15-20K | 約$0.30-0.50 |
| 3人チーム（Opus） | 50-70K | 15-20K | 約$1.50-2.50 |

:::message
**コスト削減Tips**: メンバーには Sonnet モデルを使うと約80%コスト削減できます。リーダーのみ Opus を使うのが効果的です。

現在の仕様では、**チームリーダーは起動時のセッションモデル（通常 Opus）で動作し、メンバー（teammate）は自動的に Sonnet で spawn されます**。つまり、特別な設定をしなくても、デフォルトでリーダー=Opus、メンバー=Sonnet のコスト最適構成になっています。

自分のセッションがどのモデルで動いているかは、Claude Code 起動時に `/model` コマンドで確認・変更できます。
:::

## パフォーマンス

- **並行処理**: 複数のタスクを同時進行できるため、全体の所要時間を短縮
- **コンテキスト分離**: 各エージェントが独立したコンテキストを持つため、大規模プロジェクトでもコンテキスト不足が起きにくい
- **メッセージング効率**: inbox 方式により、必要な情報のみを共有

---

# 実務で使えるチーム構成パターン

## 1. フルスタックアプリ開発チーム（4人）

```
- アーキテクト担当: システム設計・API設計
- フロントエンド担当: UI実装
- バックエンド担当: API・DB実装
- セキュリティ・QA担当: 脆弱性診断・テスト
```

**適用シーン**: 新規Webアプリケーション開発、機能追加

## 2. レガシーコード改善チーム（3人）

```
- 分析担当: 技術的負債の調査・影響範囲分析
- リファクタリング担当: コード改善実装
- テスト担当: 回帰テスト・品質保証
```

**適用シーン**: レガシーシステムのモダナイゼーション、技術的負債の返済

## 3. セキュリティ監査チーム（3人）

```
- 脆弱性診断担当: OWASP Top 10 チェック
- コードレビュー担当: セキュアコーディング検証
- 報告書作成担当: 改善提案・ドキュメント作成
```

**適用シーン**: セキュリティ監査、脆弱性診断、ペネトレーションテスト

## 4. 新機能開発チーム（5人）

```
- プロダクトオーナー: 要件定義・仕様策定
- UI/UXデザイナー: デザイン・プロトタイプ
- 実装担当（×2名）: 並行開発
- 統合テスト担当: E2Eテスト・パフォーマンス検証
```

**適用シーン**: 大規模な新機能開発、複雑な要件の実装

---

# ベストプラクティス

## チーム構成

1. **役割を明確に分ける**: 各メンバーの責任範囲を明確にする
2. **適切なモデルを選ぶ**: リーダーは Opus、メンバーは Sonnet でコスト最適化
3. **タスク依存関係を設計**: `blockedBy` を活用して順序を制御

## コミュニケーション

1. **具体的なメッセージ**: メンバー間のメッセージは具体的に
2. **進捗報告**: 各タスク完了時にリーダーへ報告
3. **仕様の共有**: デザイン仕様やAPI仕様は明示的に共有

## プロジェクト管理

1. **CLAUDE.md でチームテンプレート定義**: よく使う構成を記載
2. **プロンプト履歴を記録**: 成功したチーム作成プロンプトを保存
3. **ダッシュボードで監視**: リアルタイムで作業状況を把握

---

# まとめ

Claude Code Agent Teams は、複数の AI エージェントが協力して作業を進める革新的な機能です。

## 主なメリット

✅ **並行処理による効率化**: 複数タスクを同時進行で短時間化
✅ **コンテキスト分離**: 各エージェントが独立したコンテキストを持ち、大規模プロジェクトに対応
✅ **柔軟なチーム構成**: プロンプトで自由にチームを編成
✅ **実務レベルの品質**: セキュリティ・QA・レビュー体制を組み込める

## 今後の展望

Agent Teams はまだ実験的機能ですが、今後の進化が期待されます：

- チームテンプレート機能の追加
- セッション復元機能の改善
- GUI ベースのダッシュボード
- より高度なタスク管理機能

## 参考資料

- [Claude Code Agent Teams 公式ドキュメント](https://code.claude.com/docs/en/agent-teams)
- [Claude Code Agent Teams: Multi-Claude Orchestration](https://claudefa.st/blog/guide/agents/agent-teams)
- [Claude Code Swarm Orchestration Skill](https://gist.github.com/kieranklaassen/4f2aba89594a4aea4ad64d753984b2ea)

---

**Happy Coding with Agent Teams! 🤖✨**
