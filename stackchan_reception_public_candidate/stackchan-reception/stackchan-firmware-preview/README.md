# 本体ファームウェアの改修ソース

M5Stack公式の[StackChan](https://github.com/m5stack/StackChan)を土台に、撮影前のライブプレビュー、緑枠、タッチ撮影ボタン、保持画像の利用とプレビュー状態照会を追加しています。既存の撮像・画像変換・送信、表示、サーボ制御を再利用しています。

## 収録ファイル

`firmware/main/hal/board/stackchan_camera.{h,cc}`、`stackchan_display.{h,cc}`、`firmware/main/hal/hal_mcp.cpp`の5ファイルと、上流の`firmware/LICENSE`を収録しています。モバイルアプリやサーバー等、今回変更していない上流コードは重複収録していません。

本フォルダーはプレビュー改修版です。撮影後に作成したプレビュー停止・首振り反復対策の未適用候補は含めていません。動画では音声で撮影を指示しており、タッチ撮影操作は実演していません。

## 再現方法

1. 公式リポジトリを別フォルダーへcloneし、`eff0d6b5109663faec8f1d98848b4b0e5246bbfc`をcheckoutします。
2. 本フォルダーの`firmware/main/`以下の5ファイルを、同じ相対パスへコピーします。
3. 上流の`firmware/README.md`、`fetch_repos.py`、`repos.json`に従い依存を取得します。
4. ESP-IDF v5.5.4で、StackChan K151の設定を確認してビルドします。
5. 書込み前に、現在の動作版・パーティション・設定を保存し、対象デバイスを確認します。

端末固有の`sdkconfig`、接続設定、鍵、実機バイナリは公開対象に含めません。上流の著作権表示とLICENSEは残しています。
