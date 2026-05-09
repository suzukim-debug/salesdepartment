# Sales Outreach System
Web広告代理店向け テレアポ支援・自動メール送信システム（全8ステップ）

## システム全体フロー

```
① リスト収集              企業HP・SNS・求人サイトをスクレイピング
        ↓
② AI分析・スコアリング    SNS活用度・予算規模・求人情報を自動判定 (0〜100点)
        ↓
③ カスタマイズメール生成  Claude APIで企業別にパーソナライズ
        ↓
④ Gmail API自動送信       スケジュール送信対応
        ↓
⑤ 反響検知               開封・クリック・Gmail返信・フォーム回答を監視
        ↓
⑥ テレアポリスト優先化    スコア順でGoogleスプレッドシートに書き出し
    ├─ 反響なし → 7日後にフォローアップメール自動再送（最大2回）
    └─ 反響あり ↓
⑦ テレアポ実施            優先度付きコール管理画面で架電
        ↓
⑧ 結果記録・分析          Googleスプレッドシートに自動保存・KPI集計
```

## セットアップ

```bash
# 1. 依存パッケージのインストール
pip install -r requirements.txt

# 2. 環境変数の設定
cp .env.example .env
# .envを編集

# 3. Gmail API設定（初回のみ）
python -m gmail_integration.auth
# ブラウザが開くのでGoogleアカウントで認証 → token.json が生成される

# 4. ダッシュボード起動
uvicorn dashboard.app:app --reload
# → http://localhost:8000
```

## Google API 設定手順

### Gmail API
1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクト作成
2. Gmail API を有効化
3. OAuth 2.0 クライアントID（デスクトップアプリ）を作成
4. `credentials.json` をダウンロードしてプロジェクトルートに配置
5. `python -m gmail_integration.auth` を実行して認証

### Google Sheets
- **推奨**: `service_account.json`（サービスアカウントキー）を配置
  - スプレッドシートをサービスアカウントのメールアドレスと共有
- **簡易**: Gmail認証と同じ `token.json` を流用（自分のスプレッドシートのみ）
- `SHEETS_SPREADSHEET_ID` に対象スプレッドシートのIDを設定

## 起動

```bash
# ダッシュボード起動（スケジューラも自動起動）
uvicorn dashboard.app:app --host 0.0.0.0 --port 8000

# CLIツール
python -m cli_tools.cli --help
```

## CLI クイックリファレンス

```bash
# CSVインポート
python -m cli_tools.cli import-leads sample_leads.csv

# ホットリード確認
python -m cli_tools.cli hot-leads

# AIメールプレビュー（ID指定）
python -m cli_tools.cli preview-email 1

# 一括メール送信（スコア60以上・最大20件）
python -m cli_tools.cli send-outreach --min-score 60 --limit 20

# ドライラン（実際には送信しない）
python -m cli_tools.cli send-outreach --dry-run

# 全リード再スコアリング
python -m cli_tools.cli rescore
```

## スコアリングロジック（②）

| 要素 | 最大点 |
|------|--------|
| 業種適合度（EC・コスメ・アパレル等） | 25点 |
| SNS保有数（Instagram/TikTok/YouTube等） | 15点 |
| 投稿頻度 | 15点 |
| Web広告運用中 | 12点 |
| 従業員規模 | 18点 |
| マーケター求人あり（スクレイピング） | +ボーナス |
| Google広告タグ検出 | +ボーナス |
| 連絡先情報の充実度 | 7点 |

**70点以上** → 高優先度（赤バッジ）  
**40〜69点** → 中優先度（黄バッジ）  
**40点未満** → 低優先度

## 自動化スケジューラ（④⑤⑦フォローアップ）

| ジョブ | 実行タイミング | 内容 |
|--------|---------------|------|
| フォローアップ送信 | 毎朝9:00 | 初回メール後7日間反響なし → 自動再送（最大2回） |
| Gmail返信チェック | 15分間隔 | 返信を検知してステータス更新 |
| Sheetsエクスポート | 毎朝8:30 | ホットリスト・架電ログを自動書き出し |
| スケジュール送信 | 10分間隔 | 指定日時のメールを送信 |

## CSVインポート形式

`sample_leads.csv` を参照。テンプレートDL: ダッシュボード > 「テンプレDL」

## システム構成

```
salesdepartment/
├── config.py
├── database/
│   ├── models.py          Lead, OutreachLog, CallLog, FollowUpLog, ScrapeJob...
│   └── db.py
├── scraper/               ①リスト収集
│   ├── company_scraper.py  企業HP・SNS・Google広告タグ検出
│   ├── job_scraper.py      Indeed/Wantedlyで予算規模推定
│   ├── sns_analyzer.py     Instagramフォロワー概算
│   └── orchestrator.py     一括スクレイピング実行
├── lead_generator/        ②スコアリング
│   ├── scorer.py
│   └── csv_importer.py
├── outreach/              ③メール生成
│   ├── template_generator.py  Claude APIカスタマイズ
│   └── email_sender.py        トラッキング埋め込み
├── gmail_integration/     ④Gmail送信・⑤返信検知
│   ├── auth.py            OAuth2認証
│   ├── sender.py          スケジュール送信
│   └── monitor.py         返信・開封監視
├── tracker/               ⑤反響検知
│   └── response_tracker.py
├── sheets_integration/    ⑥⑧Sheets連携
│   ├── client.py
│   └── exporter.py        テレアポリスト・架電ログ・分析
├── scheduler/             自動化
│   ├── follow_up.py       7日後フォローアップ
│   └── runner.py          APSchedulerセットアップ
├── dashboard/             Web UI
│   ├── app.py
│   └── templates/
│       ├── index.html     ダッシュボード
│       ├── scraper.html   ①スクレイピング
│       ├── leads.html     ②リード管理
│       ├── outreach.html  ③④メール生成・送信
│       ├── hot_leads.html ⑤⑥反響・テレアポリスト
│       ├── calls.html     ⑦架電管理
│       └── analytics.html ⑧結果・分析
├── cli_tools/
│   └── cli.py
└── sample_leads.csv
```
