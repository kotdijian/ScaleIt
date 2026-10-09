"""AprilTag observations for a user-defined, measured scale-bar preset.

Detect in the same undistorted pixel geometry as the COLMAP cameras. Camera
poses are never estimated from the tags: existing COLMAP triangulation is used.
"""
from pathlib import Path
import copy
import numpy as np
from core import positive

FAMILIES = ('tag16h5','tag25h9','tag36h10','tag36h11')


def validate_preset(data):
    if not isinstance(data,dict) or data.get('version')!=1:
        raise ValueError('規定スケール設定のversionは1にしてください。')
    if data.get('type')!='apriltag' or data.get('family') not in FAMILIES:
        raise ValueError('対応方式はAprilTag: '+', '.join(FAMILIES)+'です。12bit円形コードは未対応です。')
    markers=data.get('markers',{})
    if not isinstance(markers,dict) or not 2<=len(markers)<=5:
        raise ValueError('A〜Eのうち2〜5点のタグIDを登録してください。')
    if any(p not in tuple('ABCDE') or isinstance(i,bool) or not isinstance(i,int) or i<0 for p,i in markers.items()):
        raise ValueError('点名はA〜E、タグIDは0以上の整数です。')
    if len(set(markers.values()))!=len(markers):
        raise ValueError('異なる点に同じタグIDを割り当てることはできません。')
    bars=data.get('bars',[])
    if not isinstance(bars,list) or not bars:
        raise ValueError('実測した中心間距離を最低1区間登録してください。')
    if any(not isinstance(b,dict) for b in bars):
        raise ValueError('距離設定が不正です。')
    for b in bars:
        if b.get('a') not in markers or b.get('b') not in markers or b['a']==b['b']:
            raise ValueError('距離の端点は異なる登録済み点にしてください。')
        if b.get('role') not in ('scale','check'):
            raise ValueError('距離の用途はscaleまたはcheckです。')
        try: positive(b.get('length_mm'),'中心間距離 mm')
        except (ValueError,TypeError): raise ValueError('中心間距離は正の実測値（mm）を入力してください。') from None
    if not 1<=sum(b['role']=='scale' for b in bars)<=3 or sum(b['role']=='check' for b in bars)>2:
        raise ValueError('scaleは1〜3区間、checkは0〜2区間です。')
    if set(markers)!={b[k] for b in bars for k in ('a','b')}:
        raise ValueError('登録した点は少なくとも1区間の端点に指定してください。')
    return copy.deepcopy(data)


def preset_bars(data):
    data=validate_preset(data)
    return [{'a':b['a'],'b':b['b'],'length_m':float(b['length_mm'])/1000,'role':b['role']} for b in data['bars']]


class AprilTagDetector:
    def __init__(self,family):
        try: import cv2
        except ImportError:
            raise ValueError('自動検出にはopencv-python-headlessが必要です。pip install -r requirements.txtを実行してください。') from None
        if family not in FAMILIES: raise ValueError('未対応のAprilTagファミリーです。')
        self.cv=cv2
        self.dictionary=cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco,'DICT_APRILTAG_'+family[3:]))
        params=cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod=cv2.aruco.CORNER_REFINE_SUBPIX
        # No bit correction: ambiguous/low-quality codes should be manually checked.
        params.errorCorrectionRate=0
        self.detector=cv2.aruco.ArucoDetector(self.dictionary,params)

    def detect(self,path,width,height):
        cv=self.cv
        try: raw=np.fromfile(Path(path),dtype=np.uint8)
        except OSError as e: raise ValueError(f'画像を読み込めません: {path}: {e}') from e
        image=cv.imdecode(raw,cv.IMREAD_GRAYSCALE | cv.IMREAD_IGNORE_ORIENTATION)
        if image is None: raise ValueError(f'画像を読み込めません: {path}')
        if image.shape!=(height,width):
            raise ValueError(f'画像サイズ不一致: {path}: {image.shape[::-1]} / カメラ {(width,height)}')
        corners,ids,_=self.detector.detectMarkers(image)
        found={}
        if ids is None: return found
        canonical=np.array([[-1,-1],[1,-1],[1,1],[-1,1]],dtype=np.float32)
        for tag_id,quad in zip(ids.ravel(),corners):
            tag_id=int(tag_id)
            H=cv.getPerspectiveTransform(canonical,np.asarray(quad,dtype=np.float32).reshape(4,2))
            center=cv.perspectiveTransform(np.zeros((1,1,2),dtype=np.float32),H)[0,0]
            if not np.all(np.isfinite(center)) or not (0<=center[0]<width and 0<=center[1]<height): continue
            # Keep every detection of an ID to detect duplicate physical tags.
            found.setdefault(tag_id,[]).append(center.astype(float).tolist())
        return found


def detect_scale(preset,image_root,images,progress=None,detector=None):
    """images=[(COLMAP name,width,height)]; output only real detected observations."""
    preset=validate_preset(preset)
    detector=detector or AprilTagDetector(preset['family'])
    if hasattr(detector,'dictionary') and max(preset['markers'].values())>=len(detector.dictionary.bytesList):
        raise ValueError('タグIDが選択ファミリーの範囲外です。')
    root=Path(image_root).resolve()
    observations={p:{} for p in preset['markers']}; detections=[]
    for index,(name,width,height) in enumerate(images):
        path=(root/name).resolve()
        if not path.is_relative_to(root): raise ValueError('画像パスが指定フォルダ外です。')
        found=detector.detect(path,width,height)
        accepted={}
        for p,tag_id in preset['markers'].items():
            matches=found.get(tag_id,[])
            if len(matches)>1:
                raise ValueError(f'{name}: タグID {tag_id}（点{p}）が重複しています。位置を自動選択できません。')
            if matches:
                observations[p][name]=matches[0]; accepted[p]=tag_id
        detections.append({'image':name,'markers':accepted})
        if progress: progress(index+1,len(images),name)
    return {'observations':observations,'bars':preset_bars(preset),
            'detection':{'preset':preset,'images':detections,'counts':{p:len(obs) for p,obs in observations.items()}}}
