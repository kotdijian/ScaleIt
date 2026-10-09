# 検証記録

## 2026-10-09: 画像送り・AprilTag自動計算・初期閾値10 px

Linux x86_64 / Python 3.12 / pycolmap 4.2.1 / PySide6 6.12.0 / OpenCV headless 4.14.0。

`QT_QPA_PLATFORM=offscreen python -m pytest -q`: **35 passed**。

前へ・次への端点制御、写真切替後の打点保持、新規閾値10 pxと旧session閾値の復元、規定スケール設定の検証、4種類のAprilTag（16h5・25h9・36h10・36h11）の実検出器を使用した合成画像検出、射影変換による中心位置、検出後の縮尺・検証距離計算、GUIの別スレッド一括処理を確認。未検出位置の非補完、画像寸法・破損・重複ID・ID範囲の拒否、失敗時の既存打点保持も確認。

検出評価は合成画像です。Windows 11・Mac M3での実行、実写真での検出率・測定精度、Metashape Standardでの実寸再生成は未検証。12bit円形コードは自動検出の対象外です。

## 初期版の検証

2026-10-08、Linux x86_64 / Python 3.12.14 / pycolmap 4.2.1 / PySide6 6.11.2。

`QT_QPA_PLATFORM=offscreen python -m pytest -q`: **18 passed**。

CLIによる合成sessionの再計算、COLMAPおよびXML出力も成功。倍率0.10000000000000005 m/モデル単位、A–B 100 mm、A–C 80 mm。COLMAPリグのカメラ間距離の縮尺変換、text/binaryの再読込、Qtマウスクリックを含む。

Metashape Standard実機への読み込みとメッシュ生成後の実寸、Mac M3、実写真は未検証。合成XMLは書式と変換のテスト用であり、Metashape製の実ファイルではありません。
