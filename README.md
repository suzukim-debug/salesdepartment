# Sales Outreach System
Web広告代理店向け テレアポ支援・自動メール送信システム

## 機能概要

```
CSVリスト取込
    ↓
自動スコアリング（業種・規模・SNS活用状況から0〜100点）
    ↓
Claude AIによる会社ごとのカスタマイズメール自動生成
    ↓
メール送信（開封・クリック・フォーム回答を自動トラッキング）
    ↓
反響企業がダッシュボードの「ホットリード」に自動昇格
    ↓
テレアポ架電 → 架電ログ記録 → アポ管理
```

## セットアップ

```bash
# 依存パッケージのインストール
pip install -r requirements.txt

# 環境変数の設定
cp .env.example .env
# .envを編集してAPI keyやSMTP情報を入力
```

## 起動

```bash
# ダッシュボード起動（http://localhost:8000）
uvicorn dashboard.app:app --reload

# または
python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 8000
```

## CLIツール

```bash
# CSVからリードをインポート
python -m cli_tools.cli import-leads sample_leads.csv

# リード一覧を表示（スコア50以上）
python -m cli_tools.cli list-leads --min-score 50

# ホットリード（反響あり）を表示
python -m cli_tools.cli hot-leads

# AIメールのプレビュー（送信しない）
python -m cli_tools.cli preview-email 1

# 対象リードにメール一括送信（送信前確認あり）
python -m cli_tools.cli send-outreach --min-score 60 --limit 20

# ドライラン（実際には送信しない）
python -m cli_tools.cli send-outreach --dry-run

# 全リードを再スコアリング
python -m cli_tools.cli rescore
```

## スコアリングロジック

| 要素 | 配点 |
|------|------|
| 業種（EC・コスメ・アパレル等）| 最大25点 |
| SNS保有数 | 最大15点 |
| 投稿頻度 | 最大15点 |
| Web広告運用中 | 12点 |
| 動画広告・インフルエンサー活用 | 各3〜5点 |
| 従業員規模 | 最大18点 |
| 連絡先情報の充実度 | 最大7点 |

**70点以上** → 高優先度（即アプローチ推奨）
**40〜69点** → 中優先度
**40点未満** → 低優先度

## 推奨サービス自動判定

- **インフルエンサーキャスティング**: Instagram+TikTok活用中、またはインフルエンサー活用経験あり
- **タイアップ動画広告**: YouTube活用中、または動画広告経験あり
- **SNSアカウント運用**: Instagram/Twitter/TikTokいずれかを保有
- **複合提案**: SNS未活用

## CSVインポート形式

`sample_leads.csv` を参考にしてください。
テンプレートのダウンロード: ダッシュボードの「テンプレートDL」ボタン

### 対応列名

| 列名 | 説明 |
|------|------|
| 会社名 | 必須 |
| 業種 | 例: EC・通販、美容・コスメ、アパレル |
| 都道府県 | 例: 東京都 |
| 従業員数 | 例: 10-49、100-299 |
| Instagram/Twitter/TikTok/YouTube/LINE公式 | あり/なし |
| 投稿頻度 | 毎日/週3-5/週1-3/月数回/ほぼなし/なし |
| Web広告/動画広告/インフルエンサー活用 | あり/なし |

## 環境変数

| 変数名 | 説明 |
|--------|------|
| `ANTHROPIC_API_KEY` | Claude API キー |
| `SMTP_HOST` / `SMTP_PORT` | メール送信サーバー |
| `SMTP_USER` / `SMTP_PASSWORD` | メール認証情報 |
| `BASE_URL` | 外部公開URL（トラッキング用） |
| `DATABASE_URL` | DBパス（デフォルト: SQLite） |

## システム構成

```
salesdepartment/
├── config.py                   # 設定
├── database/
│   ├── models.py               # Lead, OutreachLog, CallLog, TrackingEvent
│   └── db.py                   # SQLAlchemy セッション管理
├── lead_generator/
│   ├── scorer.py               # スコアリングエンジン
│   └── csv_importer.py         # CSVインポート
├── outreach/
│   ├── template_generator.py   # Claude AIメール生成
│   └── email_sender.py         # メール送信 + トラッキング埋め込み
├── tracker/
│   └── response_tracker.py     # 開封・クリック・フォーム記録
├── dashboard/
│   ├── app.py                  # FastAPI アプリ
│   └── templates/              # Jinja2 HTMLテンプレート
├── cli_tools/
│   └── cli.py                  # Click CLI
└── sample_leads.csv            # サンプルデータ
```
