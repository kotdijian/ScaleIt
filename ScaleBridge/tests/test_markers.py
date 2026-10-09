import numpy as np
import pytest
import cv2
from core import solve
from markers import AprilTagDetector, detect_scale, validate_preset
from test_core import fixture_model


def preset():
    return {'version':1,'type':'apriltag','family':'tag36h11','name':'Synthetic test only',
        'markers':{'A':0,'B':1,'C':2},
        'bars':[{'a':'A','b':'B','length_mm':100,'role':'scale'},
                {'a':'A','b':'C','length_mm':80,'role':'check'}]}


def tag_images(model,folder,family='tag36h11'):
    folder.mkdir()
    points={'A':np.array([0.,0.,5.]),'B':np.array([1.,0.,5.]),'C':np.array([0.,.8,5.])}
    dictionary=AprilTagDetector(family).dictionary
    for name in model.images:
        image=np.full((1200,1600),255,np.uint8)
        for tag_id,xyz in enumerate(points.values()):
            src=np.array([[0,0],[159,0],[159,159],[0,159]],np.float32)
            xyz_corners=[xyz+np.array(offset) for offset in [(-.2,-.2,0),(.2,-.2,0),(.2,.2,0),(-.2,.2,0)]]
            dst=np.array([model.project(name,q) for q in xyz_corners],np.float32)
            H=cv2.getPerspectiveTransform(src,dst)
            marker=cv2.aruco.generateImageMarker(dictionary,tag_id,160)
            warped=cv2.warpPerspective(marker,H,(1600,1200),borderValue=255)
            image=np.minimum(image,warped)
        cv2.imencode('.png',image)[1].tofile(folder/name)
    return points


@pytest.mark.parametrize('family',['tag16h5','tag25h9','tag36h10','tag36h11'])
def test_real_tag_detection_scale_and_holdout(tmp_path,family):
    model=fixture_model(tmp_path/'model'); root=tmp_path/'画像'
    points=tag_images(model,root,family)
    config=preset(); config['family']=family
    calls=[]
    payload=detect_scale(config,root,[(n,1600,1200) for n in model.images],
        progress=lambda *args:calls.append(args))
    # C lies outside image_4: missing views must not be invented by reprojection.
    assert payload['detection']['counts']=={'A':4,'B':4,'C':3}
    assert len(calls)==4 and calls[-1][:2]==(4,4)
    for p,obs in payload['observations'].items():
        for n,xy in obs.items(): np.testing.assert_allclose(xy,model.project(n,points[p]),atol=.6)
    result=solve(model,payload['observations'],payload['bars'])
    assert result['meters_per_model_unit']==pytest.approx(.1,rel=.005)
    assert abs(result['distances'][1]['relative_error_percent'])<.5


def test_perspective_center_is_not_corner_average(tmp_path):
    dictionary=AprilTagDetector('tag36h11').dictionary
    marker=cv2.aruco.generateImageMarker(dictionary,0,200)
    src=np.array([[0,0],[199,0],[199,199],[0,199]],np.float32)
    dst=np.array([[100,90],[390,150],[340,330],[140,390]],np.float32)
    H=cv2.getPerspectiveTransform(src,dst)
    image=cv2.warpPerspective(marker,H,(500,500),borderValue=255)
    path=tmp_path/'perspective.png'; cv2.imencode('.png',image)[1].tofile(path)
    detection=AprilTagDetector('tag36h11').detect(path,500,500)
    center=np.array(detection[0][0])
    expected=cv2.perspectiveTransform(np.array([[[99.5,99.5]]],np.float32),H)[0,0]
    np.testing.assert_allclose(center,expected,atol=1.2)
    assert np.linalg.norm(center-dst.mean(0))>10


def test_preset_validation_and_duplicate_tags(tmp_path):
    config=preset(); config['markers']['B']=0
    with pytest.raises(ValueError,match='同じタグID'): validate_preset(config)
    config=preset(); config['bars'][0]['length_mm']=0
    with pytest.raises(ValueError,match='実測値'): validate_preset(config)
    config=preset(); config['type']='circular12'
    with pytest.raises(ValueError,match='12bit'): validate_preset(config)
    class Duplicate:
        def detect(self,*args): return {0:[[10,20],[30,40]]}
    with pytest.raises(ValueError,match='重複'):
        detect_scale(preset(),tmp_path,[('test.png',100,100)],detector=Duplicate())
    config=preset(); config['markers']['C']=10000
    with pytest.raises(ValueError,match='範囲外'):
        detect_scale(config,tmp_path,[])


def test_blank_corrupt_and_wrong_size(tmp_path):
    detector=AprilTagDetector('tag36h11')
    path=tmp_path/'blank.png'; cv2.imencode('.png',np.full((200,300),255,np.uint8))[1].tofile(path)
    assert detector.detect(path,300,200)=={}
    with pytest.raises(ValueError,match='サイズ不一致'): detector.detect(path,301,200)
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='読み込めません'): detector.detect(path,300,200)
