# ScaleIt

写真測量データの縮尺を設定し、アプリ間で受け渡すためのツールです。

## Scale Bridge

COLMAPのカメラ情報と対応画像から、既知距離による一様縮尺を計算します。縮尺補正済みCOLMAPと、Metashape向けのAgisoftカメラXMLを出力します。

- [操作・セットアップガイド](ScaleBridge/README.md)
- [Macでの起動](ScaleBridge/README.md#macでの起動)
- [Windows 11での起動](ScaleBridge/README.md#windows-11での起動)
- [アルゴリズム](ScaleBridge/ALGORITHM.md)
- [検証記録](ScaleBridge/TEST_RESULTS.md)

Mac M3・Python 3.12・Qt 6.12.0でGUI起動を確認済みです。Metashape Standardとの往復・実寸保持は実機検証が必要です。
