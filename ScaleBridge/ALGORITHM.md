# アルゴリズムと書式 — Scale Bridge 0.1.0

## 1. 方針

カメラの内部・外部パラメータを固定し、手動で指定した物理点だけを新しく三角測量します。画像の特徴抽出、写真の再アラインメント、カメラの再最適化は行いません。各点の3D復元と、一つの全体縮尺を分けて計算します。スケールと対象物は撮影中に同じ剛体として静止している必要があります。

## 2. COLMAPの規約と書式

公式リーダー/ライターおよびレンズモデル実装はpycolmap 4.2.1を使用します。独自のレンズ係数の読み替えはしません。

`cameras.txt`:

```text
# CAMERA_ID MODEL WIDTH HEIGHT PARAMS[]
1 PINHOLE 1600 1200 1200 1190 800 600
```

この例のパラメータはfx, fy, cx, cyです。IDは連続するとは限りません。画像からCAMERA_IDを参照します。他のモデルはそれぞれのパラメータ順で解釈します。

`images.txt`（各画像につき2行。第2行は空でも存在する）:

```text
# IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME
1 1 0 0 0 2 0 0 1 image_1.png

```

Hamilton quaternionを使用します。世界→カメラの変換は

\[
\mathbf{x}_c=R\mathbf{X}+\mathbf{t},\qquad
\mathbf{C}=-R^T\mathbf{t}.
\]

カメラ軸はX右・Y下・Z前です。画像の左上画素中心は(0.5,0.5)という角基準の画素座標を使います。Qtの画像領域も左上角を(0,0)とし、表示縮尺を逆変換したscene座標を記録します。元の画像をリサイズ・EXIF回転して座標を変えません。

`points3D.txt`:

```text
# POINT3D_ID X Y Z R G B ERROR TRACK[]
# TRACK entries: IMAGE_ID POINT2D_IDX
```

任意の手動点の三角測量には既存点群は不要です。存在する点群、色、track、2D対応は書き出しで保持します。欠けたpoints3D.txtは空の点群として扱います。binary入力は完全なsparseモデルを必要とし、出力はtextです。

新しいCOLMAPの`rigs`/`frames`も公式APIで扱います。画像poseに加え、rigのセンサー間距離、frame pose、点群に同じ縮尺を与えます。古い3ファイル形式は公式APIがtrivial rigs/framesを補完します。

## 3. 3D点の推定

各観測(u,v)を公式カメラモデルの`cam_from_img`で歪みを逆補正し、正規化画像座標(x,y)を得ます。

\[
\mathbf{d}_i = \frac{R_i^T(x_i,y_i,1)^T}{\|R_i^T(x_i,y_i,1)^T\|}.
\]

初期点は、カメラ中心からの視線への直交距離二乗を最小化します。

\[
P_i=I-\mathbf{d}_i\mathbf{d}_i^T,\qquad
\mathbf{X}_0=(\sum_i P_i)^{-1}\sum_i P_i\mathbf{C}_i.
\]

その後、歪みを含む画像への再投影残差をSciPy least_squaresのsoft_l1損失で最小化します。f_scale=1pxです。カメラは変更しません。

出力の前に、各点について次を確認します。

- 異なる画像から最低2観測、全て画像領域内
- 視線対の最大の鋭角交会角がしきい値以上（初期値1°）
- 初期連立行列の条件数が1e10以下
- 解の収束、有限座標、全観測に対してカメラ前方
- 全観測の再投影距離がしきい値以下（初期値10px）

外れ値を自動削除して成功扱いにはしません。soft_l1で解を求めた後も全観測に上限を適用し、超えた写真名と誤差を表示します。2観測だけの場合は冗長性不足を注意表示します。

## 4. 一様縮尺

復元距離dと実寸L（m）からs=L/dを計算します。複数の縮尺決定区間がある場合は、固定した復元点に対し

\[
\min_{s>0}\sum_j w_j(sd_j-L_j)^2,
\qquad
s=\frac{\sum_j w_jd_jL_j}{\sum_j w_jd_j^2}.
\]

GUIの重みは全て1です。CLI sessionでは正のweightを指定できます。距離の共分散は推定しないため、この重みを統計的な信頼度と解釈しません。check区間はこの計算から除外し、sd-Lと相対誤差を記録します。同じスケール上の区間は誤差が相関し得るため、別のスケール/資料寸法による検証が望まれます。

## 5. COLMAP出力

出力単位mではa=s、mmではa=1000sとして世界座標をX'=aXに変換します。カメラ回転・内部パラメータは不変です。単眼カメラではt'=atとなります。rig内のセンサーtranslationもa倍にします。pycolmap.Reconstruction.transform(Sim3d)を利用します。

標準COLMAPに物理単位の明示フィールドはないので、`scale_metadata.json`にm/mm、倍率、入力fingerprintを記録します。座標値をmmにしたデータをmと解釈するアプリでは1000倍の差が生じるので、受け側の単位も合わせます。

画像はコピーしません。出力カメラは入力画像と同じ内部パラメータ・画像名を持ちます。点群があれば合わせて補正します。既存メッシュ・depth map・3DGSはこの処理で自動補正されません。

## 6. Metashape XML

公開マニュアルはAgisoft XMLの入出力を示していますが、安定した公開XSDと全バージョンの読み込み挙動をこの作業では確認できていません。そのため、XMLをゼロから生成して係数を変換する方法を避け、**Metashapeから書き出した元XMLを保持して外部パラメータを補正**します。

対象構造の例（値は説明用）:

```xml
<document version="2.0.0">
  <chunk id="0">
    <sensors>
      <sensor id="0" type="frame">
        <resolution width="1600" height="1200" />
        <calibration type="frame" class="adjusted">
          <resolution width="1600" height="1200" />
          <f>1190</f><b1>10</b1><b2>0</b2>
          <cx>0</cx><cy>0</cy>
        </calibration>
      </sensor>
    </sensors>
    <cameras>
      <camera id="0" label="image_1.png" sensor_id="0">
        <transform>1 0 0 -1 0 1 0 0 0 0 1 0 0 0 0 1</transform>
        <orientation>1</orientation>
      </camera>
    </cameras>
    <transform>
      <rotation>1 0 0 0 1 0 0 0 1</rotation>
      <translation>0 0 0</translation><scale>1</scale>
    </transform>
  </chunk>
</document>
```

cameraのtransformは、内部チャンク座標へのcamera-to-worldの4×4剛体行列です。camera中心は第4列のXYZです。chunkのtransformは内部座標から外部/参照座標への変換であり、cameraのtransformと同一ではありません。

Metashapeの現行frameモデルではfはピクセル、cx/cyは画像中心からのオフセット、b1/b2はaffinity/skewです。COLMAPのcx/cyは左上基準であり、同名タグを単純にコピーできません。Metashapeのp1/p2の並びも一般的なOpenCVモデルとの読み替えを要するため、**元XMLのsensors（initial/adjustedを含む）をそのまま保持**します。COLMAPを直接Metashapeキャリブレーションに変換する機能はこの版にはありません。

XML内部中心XとCOLMAP中心Yの対応は同名画像を使い、Umeyamaの相似変換

\[
\mathbf{Y}_i\approx kQ\mathbf{X}_i+\mathbf{b}
\]

を推定します。最低3つの非共線中心を必要とします。回転は反射を許さず、カメラ中心配置の正規化RMSが1e-4を超えれば拒否します。XMLカメラの回転もQを介してCOLMAPと照合し、最大角度差0.1°を超えれば拒否します。これらは書き出しの一致判定値で、測定精度の保証ではありません。

XML camera translationをks倍にし、rotationは不変とします。chunk rotationは保持しますが、chunk translationを0、chunk scaleを1にリセットし、二重の縮尺適用を避けます。存在するregion center/sizeもks倍にします。camera referenceは無効化します。出力座標はmです。元XMLの移動量・参照位置の保持は目的に含めません。

局所モデル以外のCRS、marker、ground control、rig/component等の未対応構造は拒否します。未対応XMLを推測で変換しません。全aligned cameraの対応、adjusted calibrationの存在、4×4剛体行列も確認します。実機での読み込み・生成・距離検証は残っています。

## 7. 検証の範囲

数値テスト: 合成3D点の復元、レンズモデル5種類、カメラ回転、打点ノイズ、外れ値拒否、退化配置、2視点、重み付き複数距離、holdout除外、COLMAP text/binary往復、m/mm、rig、元XMLの中心と回転、縮尺換算、同名配置/姿勢不一致の拒否。

GUIテスト: offscreen Qtでの起動、実際のマウスクリックによる画素座標の取得、写真切り替え、複数写真の打点→縮尺計算、入力変更による計算結果の無効化。

未検証: 実際のMetashape Export Cameras XML、StandardへのImport Cameras、depth map/mesh生成後の実寸、Apple Siliconのインストール/GUI、実写真での精度。v0.1はこれらを確かめるための初期実装です。

## 8. 一次資料

- COLMAP Output Format: https://colmap.github.io/format.html
- PyCOLMAP API（4.2.1で実行確認）: https://colmap.github.io/pycolmap/pycolmap.html
- Metashape Standard 2.3 manual（カメラ入出力、Appendix D camera models）: https://www.agisoft.com/pdf/metashape_2_3_en.pdf
- Agisoft Support: camera XMLの内部/外部変換の区別: https://www.agisoft.com/forum/index.php?topic=2733.0
- Agisoft Support: XML camera-to-world行列とchunk transform: https://www.agisoft.com/forum/index.php?topic=3895.0

確認日: 2026-10-08。公開資料で確認した書式と、この環境で確認した計算・I/Oを区別しています。
