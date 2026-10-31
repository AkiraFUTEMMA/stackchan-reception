# StackChan K151のカメラプレビュー改修

[M5Stack StackChan](https://github.com/m5stack/StackChan)のファームウェアに、撮影前のライブプレビュー、緑枠、タッチ撮影ボタン、撮影画像の保持とプレビュー状態照会を追加しています。

## 改修ファイル

| ファイル | 内容 |
|---|---|
| `firmware/main/hal/board/stackchan_camera.h` / `.cc` | プレビューの取得と撮影画像の管理 |
| `firmware/main/hal/board/stackchan_display.h` / `.cc` | プレビュー・緑枠・撮影ボタンの表示 |
| `firmware/main/hal/hal_mcp.cpp` | プレビューの開始・停止・状態照会のMCP操作 |

## ビルド方法

1. [公式リポジトリ](https://github.com/m5stack/StackChan)をcloneし、リビジョン`eff0d6b5109663faec8f1d98848b4b0e5246bbfc`をcheckoutします。
2. このフォルダーの`firmware/main/`以下の5ファイルを、同じ相対パスへコピーします。
3. 上流の`firmware/README.md`、`fetch_repos.py`、`repos.json`に従って依存を取得します。
4. ESP-IDF v5.5.4でStackChan K151用の設定を選び、ビルドします。
5. 使用する実機のパーティションと設定を確認して書き込みます。

## ライセンス

上流の著作権表示と`firmware/LICENSE`を保持しています。
