# 検証記録

## 2026-10-09: Metashape単一component XMLの出力修正

`QT_QPA_PLATFORM=offscreen python -m pytest -q`: **43 passed**（Linux / Python 3.12）。

単一componentのカメラ参照、chunk・component双方のregion、カメラ位置共分散の倍率2乗補正、rotationとセンサー設定の保持を検証。拡張子なしのXMLラベル、Metashapeの末尾数値付きCOLMAPファイル名の一意な対応に対応し、複数候補・誤った名前対応・複数component・component固有のtransform・不正参照は拒否することを確認。

利用者提供の実XML（242台、単一component、4センサー、文書version 1.2.0）も、XMLのカメラに既知の相似変換を適用して作った合成COLMAPで出力検証。242台すべての位置・回転、位置共分散、両region、全センサー情報を確認。カメラ配置の相対RMSは約1.74e-15。これは構造・変換の検証であり、実測スケールの精度評価ではありません。利用者の実COLMAP、Windows実行、MetashapeでのImport Camerasとメッシュ再生成後の実寸は未検証です。

元の実XMLはリポジトリへ追加していません。

## 2026-10-09: Mac M3でのGUI起動と環境復旧

利用者による実機確認: Apple M3 MacBook Air / arm64 / macOS 27.0 / Homebrew Python 3.12.15 / PySide6・Qt 6.12.0。仮想環境は `~/venvs/scalebridge`。

元のリポジトリ内仮想環境では、cocoaプラグインの実体・arm64対応・メタデータ取得・QPluginLoaderによる直接ロードを確認できた一方、QFileInfoが3つのplatformプラグインをすべて `hidden=True` と判定した。QDirのFiles指定は空、Hidden/Systemを含めると3ファイルを列挙した。一時ディレクトリの通常ファイル列挙は正常だった。

外部仮想環境でQt 6.8.3の起動を確認後、元のパッケージ構成とQt 6.12.0を通常環境へ復元し、`env -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH python gui.py` でGUI起動を確認した。Qtのダウングレードは通常運用には不要。隠し判定の起源（ファイル属性・親フォルダ・同期機能等）は未確定。

この確認の範囲はインストールとGUI起動。Mac上での全テスト実行、実写真での打点・AprilTag検出率・測定精度、Metashape Standardとの往復は未検証。

## 2026-10-09: 画像送り・AprilTag自動計算・初期閾値10 px

Linux x86_64 / Python 3.12 / pycolmap 4.2.1 / PySide6 6.12.0 / OpenCV headless 4.14.0。

`QT_QPA_PLATFORM=offscreen python -m pytest -q`: **35 passed**。

前へ・次への端点制御、写真切替後の打点保持、新規閾値10 pxと旧session閾値の復元、規定スケール設定の検証、4種類のAprilTag（16h5・25h9・36h10・36h11）の実検出器を使用した合成画像検出、射影変換による中心位置、検出後の縮尺・検証距離計算、GUIの別スレッド一括処理を確認。未検出位置の非補完、画像寸法・破損・重複ID・ID範囲の拒否、失敗時の既存打点保持も確認。

検出評価はLinux上の合成画像です。Windows 11での実行、Mac M3での同等の計算・検出テスト、実写真での検出率・測定精度、Metashape Standardでの実寸再生成は未検証。Mac M3のGUI起動確認は上の記録を参照してください。12bit円形コードは自動検出の対象外です。

## 初期版の検証

2026-10-08、Linux x86_64 / Python 3.12.14 / pycolmap 4.2.1 / PySide6 6.11.2。

`QT_QPA_PLATFORM=offscreen python -m pytest -q`: **18 passed**。

CLIによる合成sessionの再計算、COLMAPおよびXML出力も成功。倍率0.10000000000000005 m/モデル単位、A–B 100 mm、A–C 80 mm。COLMAPリグのカメラ間距離の縮尺変換、text/binaryの再読込、Qtマウスクリックを含む。

Metashape Standard実機への読み込みとメッシュ生成後の実寸、Mac M3、実写真は未検証。合成XMLは書式と変換のテスト用であり、Metashape製の実ファイルではありません。
