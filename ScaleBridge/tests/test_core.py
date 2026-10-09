import json
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pycolmap
import pytest
from core import Model, solve, triangulate, export_colmap, export_metashape


def fixture_model(path, camera_model='PINHOLE',params=None,points=True):
    path.mkdir()
    params=params or [1200,1190,800,600]
    (path/'cameras.txt').write_text(f'1 {camera_model} 1600 1200 '+' '.join(map(str,params))+'\n')
    centers=[[-2,0,0],[2,0,0],[0,2,0],[0,-2,0]]
    lines=[]
    for i,c in enumerate(centers,1):
        lines.extend([f'{i} 1 0 0 0 {-c[0]} {-c[1]} {-c[2]} 1 image_{i}.png',''])
    (path/'images.txt').write_text('\n'.join(lines)+'\n')
    if points:(path/'points3D.txt').write_text('7 0 0 5 10 20 30 0\n')
    return Model(path)


def obs(model,point,names=None):
    return {n:model.project(n,np.array(point)).tolist() for n in (names or model.images)}


def make_xml(path,model,k=2,offset=(10,20,30)):
    root=ET.Element('document',version='2.0.0'); chunk=ET.SubElement(root,'chunk',id='0')
    sensors=ET.SubElement(chunk,'sensors'); sensor=ET.SubElement(sensors,'sensor',id='0',type='frame')
    ET.SubElement(sensor,'resolution',width='1600',height='1200')
    calib=ET.SubElement(sensor,'calibration',type='frame',attrib={'class':'adjusted'})
    ET.SubElement(calib,'resolution',width='1600',height='1200')
    for tag,value in [('f','1190'),('b1','10'),('b2','0'),('cx','0'),('cy','0'),('k1','0.0001')]:
        ET.SubElement(calib,tag).text=value
    cameras=ET.SubElement(chunk,'cameras')
    theta=.3; Q=np.array([[np.cos(theta),-np.sin(theta),0],[np.sin(theta),np.cos(theta),0],[0,0,1]])
    for i,n in enumerate(model.images):
        camera=ET.SubElement(cameras,'camera',id=str(i),label=n,sensor_id='0')
        M=np.eye(4); M[:3,:3]=Q.T; M[:3,3]=Q.T@(model.center(n)-offset)/k
        ET.SubElement(camera,'transform').text=' '.join(map(str,M.flat))
        ET.SubElement(camera,'orientation').text='1'
    tr=ET.SubElement(chunk,'transform')
    ET.SubElement(tr,'rotation').text='1 0 0 0 1 0 0 0 1'
    ET.SubElement(tr,'translation').text='100 200 300'; ET.SubElement(tr,'scale').text='17'
    region=ET.SubElement(chunk,'region'); ET.SubElement(region,'center').text='1 2 3'; ET.SubElement(region,'size').text='4 5 6'
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize('model_name,params',[
 ('PINHOLE',[1200,1190,800,600]),('SIMPLE_RADIAL',[1200,800,600,.1]),
 ('OPENCV',[1200,1190,800,600,.1,-.02,.001,-.002]),
 ('FULL_OPENCV',[1200,1190,800,600,.1,-.02,.001,-.002,.001,.002,-.001,.0001]),
 ('OPENCV_FISHEYE',[1200,1190,800,600,.01,-.002,.001,-.0001])])
def test_lens_triangulation(tmp_path,model_name,params):
    model=fixture_model(tmp_path/'model',model_name,params)
    target=np.array([.12,.15,5.1]); r=triangulate(model,obs(model,target))
    assert np.allclose(r['xyz'],target,atol=1e-8)
    assert r['rms_px']<1e-7


def test_scale_and_holdout(tmp_path):
    model=fixture_model(tmp_path/'model')
    observations={p:obs(model,q) for p,q in {'A':[0,0,5],'B':[1,0,5],'C':[0,.4,5]}.items()}
    result=solve(model,observations,[{'a':'A','b':'B','length_m':.1},
                                    {'a':'A','b':'C','length_m':.04,'role':'check'}])
    assert result['meters_per_model_unit']==pytest.approx(.1)
    assert result['distances'][1]['estimated_m']==pytest.approx(.04)
    assert not result['warnings']


def test_noise_and_outlier(tmp_path):
    model=fixture_model(tmp_path/'model')
    observations=obs(model,[.1,.2,5])
    observations['image_1.png'][0]+=.2
    assert np.linalg.norm(np.array(triangulate(model,observations)['xyz'])-[.1,.2,5])<.002
    observations['image_1.png'][0]+=20
    with pytest.raises(ValueError,match='Reprojection'):triangulate(model,observations)


def test_invalid_geometry(tmp_path):
    model=fixture_model(tmp_path/'model')
    with pytest.raises(ValueError,match='two images'):triangulate(model,{'image_1.png':[800,600]})
    with pytest.raises(ValueError,match='angle'):triangulate(model,{n:[800,600] for n in model.images})
    with pytest.raises(ValueError,match='outside'):triangulate(model,{n:[-1,0] for n in model.images})
    with pytest.raises(ValueError,match='behind'):model.project('image_1.png',np.array([0,0,-5]))


def test_export_roundtrip_and_binary(tmp_path):
    model=fixture_model(tmp_path/'model')
    export_colmap(model,tmp_path/'scaled',.1,'mm')
    scaled=Model(tmp_path/'scaled')
    for name in model.images:
        assert np.allclose(scaled.center(name),model.center(name)*100)
        assert np.allclose(scaled.pose(name)[0],model.pose(name)[0])
        assert np.allclose(scaled.camera(name).params,model.camera(name).params)
        assert np.allclose(scaled.project(name,np.array([0,0,500])),model.project(name,np.array([0,0,5])))
    assert np.allclose(scaled.rec.points3D[7].xyz,[0,0,500])
    binary=tmp_path/'binary'; binary.mkdir(); model.rec.write_binary(str(binary))
    imported=Model(binary)
    assert len(imported.images)==4
    with pytest.raises(ValueError,match='exists'):export_colmap(model,tmp_path/'scaled',1)


def test_camera_only(tmp_path):
    model=fixture_model(tmp_path/'model',points=False)
    assert len(model.rec.points3D)==0
    export_colmap(model,tmp_path/'out',.1)
    assert len(Model(tmp_path/'out').images)==4


def test_xml_correspondence(tmp_path):
    model=fixture_model(tmp_path/'model'); xml=tmp_path/'original.xml'; make_xml(xml,model)
    before=ET.parse(xml); output=tmp_path/'scaled.xml'
    report=export_metashape(model,xml,output,.1)
    assert report['xml_internal_multiplier']==pytest.approx(.2)
    after=ET.parse(output)
    assert before.find('./chunk/sensors/sensor/calibration/k1').text==after.find('./chunk/sensors/sensor/calibration/k1').text
    assert after.find('./chunk/transform/scale').text=='1'
    assert after.find('./chunk/transform/translation').text=='0 0 0'
    orig=[np.fromstring(c.find('transform').text,sep=' ').reshape(4,4) for c in before.findall('./chunk/cameras/camera')]
    new=[np.fromstring(c.find('transform').text,sep=' ').reshape(4,4) for c in after.findall('./chunk/cameras/camera')]
    assert np.linalg.norm(new[0][:3,3]-new[1][:3,3])==pytest.approx(.4)
    for a,b in zip(orig,new):
        assert np.allclose(a[:3,:3],b[:3,:3]); assert np.allclose(a[:3,3]*.2,b[:3,3])
    # Wrong alignment, changed baseline pattern: refuse export.
    tree=ET.parse(xml); e=tree.find('./chunk/cameras/camera/transform')
    vals=np.fromstring(e.text,sep=' '); vals[3]+=1; e.text=' '.join(map(str,vals)); tree.write(tmp_path/'bad.xml')
    with pytest.raises(ValueError,match='disagree'):export_metashape(model,tmp_path/'bad.xml',tmp_path/'badout.xml',.1)


def test_xml_reject_and_source_change(tmp_path):
    model=fixture_model(tmp_path/'model'); xml=tmp_path/'x.xml'; make_xml(xml,model)
    tree=ET.parse(xml); ET.SubElement(tree.find('chunk'),'reference').text='GEOGCS[WGS84]'; tree.write(xml)
    with pytest.raises(ValueError,match='CRS'):export_metashape(model,xml,tmp_path/'out.xml',1)
    with (model.folder/'images.txt').open('a') as f:f.write('# changed\n')
    with pytest.raises(ValueError,match='changed'):export_colmap(model,tmp_path/'out',1)


def test_nontrivial_rig_scale(tmp_path):
    p=tmp_path/'rig'; p.mkdir()
    (p/'cameras.txt').write_text('1 PINHOLE 1600 1200 1200 1200 800 600\n2 PINHOLE 1600 1200 1200 1200 800 600\n')
    (p/'rigs.txt').write_text('1 2 CAMERA 1 CAMERA 2 1 1 0 0 0 0.3 0 0\n')
    frames=[]; images=[]
    for i,x in enumerate([-2,0,2],1):
        frames.append(f'{i} 1 1 0 0 0 {-x} 0 0 2 CAMERA 1 {2*i-1} CAMERA 2 {2*i}')
        images.extend([f'{2*i-1} 1 0 0 0 {-x} 0 0 1 ref{i}.png','',
                       f'{2*i} 1 0 0 0 {-x+.3} 0 0 2 slave{i}.png',''])
    (p/'frames.txt').write_text('\n'.join(frames)+'\n'); (p/'images.txt').write_text('\n'.join(images)+'\n')
    (p/'points3D.txt').write_text('')
    model=Model(p); export_colmap(model,tmp_path/'scaled',.1)
    scaled=Model(tmp_path/'scaled')
    for n in model.images:assert np.allclose(scaled.center(n),model.center(n)*.1)


def test_rotated_cameras_and_two_view_solution(tmp_path):
    from scipy.spatial.transform import Rotation
    model=fixture_model(tmp_path/'model')
    lines=[]
    for i,n in enumerate(sorted(model.images),1):
        R=Rotation.from_euler('y',(i-2)*5,degrees=True).as_matrix()
        q=Rotation.from_matrix(R).as_quat(); t=-R@model.center(n)
        lines.extend([f'{i} {q[3]} {q[0]} {q[1]} {q[2]} {t[0]} {t[1]} {t[2]} 1 {n}',''])
    (model.folder/'images.txt').write_text('\n'.join(lines)+'\n'); model=Model(model.folder)
    target=np.array([.1,.1,5])
    r=triangulate(model,obs(model,target,list(model.images)[:2]))
    assert np.allclose(r['xyz'],target,atol=1e-8) and r['warnings']
    export_colmap(model,tmp_path/'out',.25)
    scaled=Model(tmp_path/'out')
    for n in model.images:assert np.allclose(scaled.project(n,target*.25),model.project(n,target))


def test_weighted_scale_and_validation_exclusion(tmp_path):
    model=fixture_model(tmp_path/'model')
    observations={p:obs(model,q) for p,q in {'A':[0,0,5],'B':[1,0,5],'C':[0,.5,5]}.items()}
    result=solve(model,observations,[{'a':'A','b':'B','length_m':.1,'weight':2},
      {'a':'A','b':'C','length_m':.051,'weight':1},
      {'a':'B','b':'C','length_m':100,'role':'check'}])
    assert result['meters_per_model_unit']==pytest.approx((2*.1+.5*.051)/(2+.25))
    assert result['warnings']
    with pytest.raises(ValueError,match='positive'):
        solve(model,observations,[{'a':'A','b':'B','length_m':float('nan')}])


def test_xml_orientation_mismatch_and_whitespace_name(tmp_path):
    model=fixture_model(tmp_path/'model'); xml=tmp_path/'x.xml'; make_xml(xml,model)
    tree=ET.parse(xml); element=tree.find('./chunk/cameras/camera/transform')
    M=np.fromstring(element.text,sep=' ').reshape(4,4); M[:3,:3]=np.eye(3)
    element.text=' '.join(map(str,M.flat)); tree.write(xml)
    with pytest.raises(ValueError,match='orientations'):export_metashape(model,xml,tmp_path/'out.xml',.1)
    p=model.folder/'images.txt'; p.write_text(p.read_text().replace('image_1.png','image one.png'))
    with pytest.raises(ValueError,match='whitespace'):Model(model.folder)


def component_xml(path,model):
    import copy
    make_xml(path,model)
    tree=ET.parse(path); chunk=tree.find('chunk')
    container=ET.SubElement(chunk,'components',next_id='1',active_id='0')
    component=ET.SubElement(container,'component',id='0',label='Component 1')
    component.append(copy.deepcopy(chunk.find('region')))
    partition=ET.SubElement(ET.SubElement(component,'partition'),'partition')
    ET.SubElement(partition,'camera_ids').text='0 1 2 3'
    for camera in chunk.findall('./cameras/camera'):
        camera.set('component_id','0')
        camera.set('label',Path(camera.get('label')).stem)
        ET.SubElement(camera,'location_covariance').text='1 0 0 0 2 0 0 0 3'
        ET.SubElement(camera,'rotation_covariance').text='4 0 0 0 5 0 0 0 6'
    tree.write(path)
    return tree


def test_single_component_stem_names_regions_and_covariances(tmp_path):
    model=fixture_model(tmp_path/'model'); xml=tmp_path/'original.xml'
    before=component_xml(xml,model)
    out=tmp_path/'scaled.xml'; report=export_metashape(model,xml,out,.1)
    assert report['xml_component_id']=='0'
    assert report['camera_name_matching']=={'exact':0,'stem':4,'numeric_suffix':0}
    assert report['xml_internal_multiplier']==pytest.approx(.2)
    after=ET.parse(out)
    for xpath in ['./chunk/region','./chunk/components/component/region']:
        for tag in ['center','size']:
            a=np.fromstring(before.find(xpath+'/'+tag).text,sep=' ')
            b=np.fromstring(after.find(xpath+'/'+tag).text,sep=' ')
            np.testing.assert_allclose(b,a*.2)
    def sensor_values(tree):
        return [(e.tag,e.attrib,(e.text or '').strip()) for e in tree.find('./chunk/sensors').iter()]
    assert sensor_values(before)==sensor_values(after)
    assert after.find('./chunk/components/component/partition/partition/camera_ids').text=='0 1 2 3'
    for c in after.findall('./chunk/cameras/camera'):
        assert c.get('component_id')=='0'
        np.testing.assert_allclose(np.fromstring(c.find('location_covariance').text,sep=' '),np.diag([1,2,3]).ravel()*.04)
        np.testing.assert_allclose(np.fromstring(c.find('rotation_covariance').text,sep=' '),np.diag([4,5,6]).ravel())


def test_component_numeric_suffix_and_wrong_mapping_rejection(tmp_path):
    model=fixture_model(tmp_path/'model'); xml=tmp_path/'original.xml'; component_xml(xml,model)
    source=(model.folder/'images.txt').read_text()
    for i in range(1,5): source=source.replace(f'image_{i}.png',f'image_{i}_{i+24}.jpg')
    (model.folder/'images.txt').write_text(source); model=Model(model.folder)
    report=export_metashape(model,xml,tmp_path/'scaled.xml',.1)
    assert report['camera_name_matching']['numeric_suffix']==4
    # Even a unique name match must still pass whole-layout and orientation checks.
    tree=ET.parse(xml); cams=tree.findall('./chunk/cameras/camera')
    label=cams[0].get('label'); cams[0].set('label',cams[1].get('label')); cams[1].set('label',label)
    tree.write(xml)
    with pytest.raises(ValueError,match='disagree'):export_metashape(model,xml,tmp_path/'wrong.xml',.1)
    assert not (tmp_path/'wrong.xml').exists()


@pytest.mark.parametrize('problem',['multiple','transform','wrong_reference','missing_reference','active'])
def test_unsupported_components_are_rejected(tmp_path,problem):
    model=fixture_model(tmp_path/'model'); xml=tmp_path/'x.xml'; tree=component_xml(xml,model)
    container=tree.find('./chunk/components'); component=container.find('component')
    camera=tree.find('./chunk/cameras/camera')
    if problem=='multiple': ET.SubElement(container,'component',id='1')
    elif problem=='transform': ET.SubElement(component,'transform').text='1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1'
    elif problem=='wrong_reference': camera.set('component_id','1')
    elif problem=='missing_reference': del camera.attrib['component_id']
    else: container.set('active_id','1')
    tree.write(xml)
    with pytest.raises(ValueError,match='[Cc]omponent'):export_metashape(model,xml,tmp_path/'out.xml',.1)
    assert not (tmp_path/'out.xml').exists()


def test_ambiguous_stem_names_rejected(tmp_path):
    model=fixture_model(tmp_path/'model'); xml=tmp_path/'x.xml'; component_xml(xml,model)
    source=(model.folder/'images.txt').read_text().replace('image_2.png','image_1.jpg')
    (model.folder/'images.txt').write_text(source); model=Model(model.folder)
    with pytest.raises(ValueError,match='ambiguous'):export_metashape(model,xml,tmp_path/'out.xml',.1)


def rectified_xml_fixture(tmp_path,problem=None):
    from scipy.spatial.transform import Rotation
    model=fixture_model(tmp_path/'model','SIMPLE_PINHOLE',[1200,800,600])
    xml=tmp_path/'original.xml'; before=component_xml(xml,model)
    before.find('./chunk/sensors/sensor/calibration/f').text='1200'; before.write(xml)
    tilt=Rotation.from_rotvec(np.radians([.35,-.06,0])).as_matrix()
    if problem=='large': tilt=Rotation.from_rotvec(np.radians([2,0,0])).as_matrix()
    if problem=='roll': tilt=Rotation.from_rotvec(np.radians([.35,0,.1])).as_matrix()
    lines=[]
    for i,n in enumerate(sorted(model.images),1):
        R=tilt@model.pose(n)[0]
        if problem=='nonuniform' and i==1:
            R=Rotation.from_rotvec(np.radians([.02,0,0])).as_matrix()@R
        t=-R@model.center(n); q=Rotation.from_matrix(R).as_quat()
        lines.extend([f'{i} {q[3]} {q[0]} {q[1]} {q[2]} {t[0]} {t[1]} {t[2]} 1 {n}',''])
    (model.folder/'images.txt').write_text('\n'.join(lines)+'\n')
    if problem=='focal': (model.folder/'cameras.txt').write_text('1 SIMPLE_PINHOLE 1600 1200 1300 800 600\n')
    if problem=='principal': (model.folder/'cameras.txt').write_text('1 SIMPLE_PINHOLE 1600 1200 1200 802 600\n')
    if problem=='lens': (model.folder/'cameras.txt').write_text('1 SIMPLE_RADIAL 1600 1200 1200 800 600 .1\n')
    return Model(model.folder),xml,before


def test_sensor_common_pinhole_tilt_preserves_original_image_poses(tmp_path):
    model,xml,before=rectified_xml_fixture(tmp_path)
    out=tmp_path/'scaled.xml'; report=export_metashape(model,xml,out,.1)
    assert report['max_raw_orientation_error_deg']>.3
    assert report['max_orientation_error_deg']<1e-10
    correction=report['sensor_camera_frame_adjustments'][0]
    assert correction['matched_cameras']==4 and correction['angle_deg']>.3
    after=ET.parse(out)
    for a,b in zip(before.findall('./chunk/cameras/camera'),after.findall('./chunk/cameras/camera')):
        A=np.fromstring(a.find('transform').text,sep=' ').reshape(4,4)
        B=np.fromstring(b.find('transform').text,sep=' ').reshape(4,4)
        # Retain original photograph poses, never replace them with rectified poses.
        np.testing.assert_allclose(B[:3,:3],A[:3,:3],atol=1e-15)
        np.testing.assert_allclose(B[:3,3],A[:3,3]*.2,atol=1e-12)


@pytest.mark.parametrize('problem',['nonuniform','large','roll','focal','principal','lens'])
def test_pinhole_tilt_allowance_rejects_other_pose_changes(tmp_path,problem):
    model,xml,_=rectified_xml_fixture(tmp_path,problem)
    out=tmp_path/'scaled.xml'
    with pytest.raises(ValueError,match='orientations disagree'): export_metashape(model,xml,out,.1)
    assert not out.exists()
