# 検証記録

2026-10-08、Linux x86_64 / Python 3.12.14 / pycolmap 4.2.1 / PySide6 6.11.2。

`QT_QPA_PLATFORM=offscreen python -m pytest -q`: **18 passed**。

CLIによる合成sessionの再計算、COLMAPおよびXML出力も成功。倍率0.10000000000000005 m/モデル単位、A–B 100 mm、A–C 80 mm。COLMAPリグのカメラ間距離の縮尺変換、text/binaryの再読込、Qtマウスクリックを含む。

Metashape Standard実機への読み込みとメッシュ生成後の実寸、Mac M3、実写真は未検証。合成XMLは書式と変換のテスト用であり、Metashape製の実ファイルではありません。
