# 宛名を読んで、連絡先と行き先を案内する受付ロボット

StackChan K151を使った受付ロボットです。音声で配達の用件を受け、宛名を撮影して名簿と照合し、内線番号と建物の方向を案内します。

## 動作例

1. 「森川さんへの荷物」という依頼に、下の名前か所属を尋ねる。
2. 名前の読み方が分からない場合は、宛名を本体のプレビューに映して撮影する。
3. 画像の氏名を名簿と照合し、読みと所属を示して宛先を確認する。
4. 内線番号を一桁ずつ読み、電話がある方向へ首を向けて案内する。
5. 通話後に行き先を尋ねると、建物への道順を案内する。

## 構成

| 構成要素 | 役割 |
|---|---|
| StackChan K151 | 音声の入出力、撮影、画面表示、首振り |
| 小智AI（XiaoZhi AI） | 音声認識、画像理解、対話、音声合成 |
| 受付MCPサーバー | 名簿照合、宛先の確認、内線と道順の検索 |

本体ファームウェアを改修し、撮影前のライブプレビュー、緑枠、タッチ撮影ボタンを追加しています。受付MCPはPythonで実装しています。

## セットアップ

PowerShellで以下を実行します。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe reception_server.py
```

MCPクライアントには、上記のPythonと`reception_server.py`をstdioサーバーとして登録します。小智AIとの中継接続を設定し、`personality.txt`をPersonality欄に設定してください。

本体のビルド方法は、[ファームウェアの説明](stackchan-firmware-preview/README.md)を参照してください。

## 主なファイル

| ファイル | 内容 |
|---|---|
| `reception_server.py` | MCPの入口と宛先の照合処理 |
| `reception_flow.py` | 用件・宛先・確認と訂正の管理 |
| `facility_engine.py` / `facility_tools.py` | 施設・研修・利用ルールの検索 |
| `guidance_motion.py` | 来訪者視点の方向と首振り角度の対応 |
| `document_intake.py` | 文書の種類と訪問目的の照合 |
| `reception_data.json` / `facility_data.json` | 名簿、内線、道順、施設・研修情報 |
| `personality.txt` | ロボットの応対と操作の指示 |
| `sample_recipient_label.pdf` | 動作例に使用する宛名 |
| `stackchan-firmware-preview/` | 本体ファームウェアの改修ソース |
| `test_*.py` | 単体・回帰試験 |

同梱の施設・職員・所属・内線は、デモ用の架空データです。

## テスト

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -p "test_*.py"
```

## 使用したプロジェクト

- [M5Stack StackChan](https://github.com/m5stack/StackChan) — 本体ファームウェアの改修元。
- [小智AI](https://xiaozhi.me/) — 音声対話・画像理解サービス。

上流コードの著作権表示とLICENSEは各ソースおよび`stackchan-firmware-preview/firmware/LICENSE`に記載しています。
