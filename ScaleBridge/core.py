"""Scale Bridge: fixed-camera triangulation and uniform metric scaling."""
from __future__ import annotations
import hashlib
import json
import shutil
import tempfile
import re
from pathlib import Path
from dataclasses import dataclass
import xml.etree.ElementTree as ET
import numpy as np
import pycolmap
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

VERSION = '0.1.0'


def positive(value, label='value'):
    value = float(value)
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f'{label} must be finite and positive')
    return value


def fingerprint(folder):
    """Pose/calibration fingerprint (points do not determine manual triangulation)."""
    h = hashlib.sha256()
    for stem in ('cameras', 'images', 'rigs', 'frames'):
        # Same precedence as COLMAP: binary when available.
        p = Path(folder) / f'{stem}.bin'
        if not p.exists():
            p = Path(folder) / f'{stem}.txt'
        if p.exists():
            h.update(p.name.encode())
            with p.open('rb') as f:
                for block in iter(lambda: f.read(1048576), b''):
                    h.update(block)
    return h.hexdigest()


class Model:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        if not ((self.folder/'cameras.txt').exists() or (self.folder/'cameras.bin').exists()):
            raise ValueError('Select the sparse model directory containing cameras/images files.')
        self.signature = fingerprint(self.folder)
        if not (self.folder/'images.bin').exists() and (self.folder/'images.txt').exists():
            # The official C++ text reader tokenizes names on whitespace.
            with (self.folder/'images.txt').open(encoding='utf-8') as f:
                while True:
                    line=f.readline()
                    if not line: break
                    if not line.strip() or line.lstrip().startswith('#'): continue
                    fields=line.strip().split(maxsplit=9)
                    if len(fields)!=10 or any(ch.isspace() for ch in fields[9]):
                        raise ValueError('COLMAP text image filenames containing whitespace are unsupported. Rename at source and re-export.')
                    f.readline()  # Observation line, including a deliberately empty one.
        # Camera-only text exports are valid input to this application.
        if not any((self.folder/f'points3D.{e}').exists() for e in ('txt','bin')):
            if not (self.folder/'images.txt').exists() or (self.folder/'images.bin').exists():
                raise ValueError('Binary input requires a complete sparse model including points3D.bin.')
            with tempfile.TemporaryDirectory() as d:
                for n in ('cameras.txt','images.txt','frames.txt','rigs.txt'):
                    if (self.folder/n).exists():
                        shutil.copyfile(self.folder/n, Path(d)/n)
                (Path(d)/'points3D.txt').write_text('# No existing points\n')
                self.rec = pycolmap.Reconstruction(d)
        else:
            self.rec = pycolmap.Reconstruction(str(self.folder))
        registered = [im for im in self.rec.images.values() if im.has_pose]
        if len({im.name for im in registered}) != len(registered):
            raise ValueError('Duplicate/truncated image names. COLMAP text names must not contain spaces.')
        self.images = {im.name: im for im in registered}
        if len(self.images) < 2:
            raise ValueError('At least two registered images are required.')
        if len(self.images) != len(set(self.images)):
            raise ValueError('Duplicate image names.')
        if any(any(ch.isspace() for ch in name) for name in self.images):
            raise ValueError('Image names containing whitespace cannot be safely exported as COLMAP text.')
        for cam in self.rec.cameras.values():
            if not cam.verify_params() or not np.all(np.isfinite(cam.params)):
                raise ValueError('Invalid camera calibration.')

    def camera(self, name):
        return self.rec.cameras[self.images[name].camera_id]

    def pose(self, name):
        p = self.images[name].cam_from_world()
        return np.asarray(p.rotation.matrix()), np.asarray(p.translation)

    def center(self, name):
        R, t = self.pose(name)
        return -R.T @ t

    def ray(self, name, xy):
        camera = self.camera(name)
        xy = np.asarray(xy, dtype=float)
        if xy.shape != (2,) or not np.all(np.isfinite(xy)):
            raise ValueError('Invalid pixel coordinate.')
        if not (0 <= xy[0] < camera.width and 0 <= xy[1] < camera.height):
            raise ValueError(f'Point outside image: {name}')
        normalized = camera.cam_from_img(xy)
        if normalized is None or not np.all(np.isfinite(normalized)):
            raise ValueError(f'Inverse projection failed: {name}')
        R, _ = self.pose(name)
        v = R.T @ np.r_[normalized, 1.0]
        return self.center(name), v / np.linalg.norm(v)

    def project(self, name, xyz):
        R, t = self.pose(name)
        q = R @ xyz + t
        if q[2] <= 0:
            raise ValueError(f'Point lies behind camera: {name}')
        xy = self.camera(name).img_from_cam(q)
        if xy is None or not np.all(np.isfinite(xy)):
            raise ValueError('Projection failed.')
        return np.asarray(xy)


class CalculationError(ValueError):
    """Failed scale calculation retaining per-marker diagnostics for the GUI."""
    def __init__(self, points, failures):
        self.points = points
        self.failures = failures
        super().__init__('\n'.join(f'{p}: {reason}' for p,reason in failures.items()))


def triangulate(model, observations, min_angle_deg=1.0, max_error_px=10.0, *, check_error=True):
    """Observations: image name -> [x,y], one per image for one physical point."""
    if len(observations) < 2:
        raise ValueError('Each point needs observations from at least two images.')
    positive(min_angle_deg); positive(max_error_px)
    rays = [model.ray(n, xy) for n, xy in observations.items()]
    directions = np.array([v for _, v in rays])
    # Acute angle measures usable intersection geometry; 180 degrees is also degenerate.
    angles = [np.degrees(np.arccos(np.clip(abs(a @ b), 0, 1)))
              for i,a in enumerate(directions) for b in directions[i+1:]]
    angle = max(angles)
    if angle < min_angle_deg:
        raise ValueError(f'Insufficient triangulation angle: {angle:.3f} degrees.')
    projectors = [np.eye(3)-np.outer(v,v) for _,v in rays]
    M = sum(projectors)
    if np.linalg.cond(M) > 1e10:
        raise ValueError('Degenerate camera geometry.')
    x0 = np.linalg.solve(M, sum(P@c for P,(c,_) in zip(projectors,rays)))
    for n in observations:
        model.project(n,x0)  # Reject invalid/behind-camera initial solution.
    def residual(x):
        values = []
        for n, xy in observations.items():
            R,t = model.pose(n)
            q = R@x+t
            pred = model.camera(n).img_from_cam(q, check_cheirality=False)
            if pred is None or not np.all(np.isfinite(pred)):
                return np.full(2*len(observations), 1e12)
            values.extend(np.asarray(pred)-xy)
        return np.array(values)
    fit = least_squares(residual, x0, loss='soft_l1', f_scale=1.0,
                        xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=300)
    if not fit.success or not np.all(np.isfinite(fit.x)):
        raise ValueError('Triangulation refinement did not converge.')
    errors = {n: float(np.linalg.norm(model.project(n,fit.x)-xy))
              for n,xy in observations.items()}
    if check_error and max(errors.values()) > max_error_px:
        raise ValueError('Reprojection error exceeds threshold: '+
                         ', '.join(f'{n}: {e:.2f}px' for n,e in errors.items()))
    warnings = ['Only two views: add a third for redundancy.'] if len(observations)==2 else []
    return {'xyz':fit.x.tolist(),'errors_px':errors,
            'rms_px':float(np.sqrt(np.mean(np.square(list(errors.values()))))),
            'max_intersection_angle_deg':float(angle),'warnings':warnings}


def solve(model, observations, bars, min_angle_deg=1.0, max_error_px=10.0):
    """bars=[{a,b,length_m,role:'scale'|'check',weight:1}]."""
    if not bars or not any(b.get('role','scale')=='scale' for b in bars):
        raise ValueError('At least one scale distance is required.')
    for role,limit in [('scale',3),('check',2)]:
        if sum(b.get('role','scale')==role for b in bars)>limit:
            raise ValueError(f'Maximum {limit} {role} distances are supported.')
    needed = {b[k] for b in bars for k in ('a','b')}
    points = {}; failures = {}
    # Inspect every marker, even when one fails; no scale is accepted on failure.
    for p in sorted(needed | set(observations)):
        try:
            points[p] = triangulate(model,observations.get(p,{}),min_angle_deg,
                                    max_error_px,check_error=False)
            exceeded = {n:e for n,e in points[p]['errors_px'].items() if e>max_error_px}
            if exceeded:
                failures[p] = 'Reprojection error exceeds threshold: '+', '.join(
                    f'{n}: {e:.2f}px' for n,e in exceeded.items())
        except ValueError as e:
            failures[p] = str(e)
    if failures:
        raise CalculationError(points,failures)
    measurements=[]
    for b in bars:
        if b['a']==b['b'] or b.get('role','scale') not in ('scale','check'):
            raise ValueError('Invalid endpoints or role.')
        L=positive(b['length_m'],'distance'); w=positive(b.get('weight',1),'weight')
        d=np.linalg.norm(np.array(points[b['a']]['xyz'])-points[b['b']]['xyz'])
        positive(d,'reconstructed distance')
        measurements.append({**b,'length_m':L,'weight':w,'distance_model':float(d)})
    used=[b for b in measurements if b.get('role','scale')=='scale']
    # Fixed endpoints: weighted least squares for a single positive global scale.
    scale=sum(b['weight']*b['distance_model']*b['length_m'] for b in used)/sum(
              b['weight']*b['distance_model']**2 for b in used)
    positive(scale,'scale')
    for b in measurements:
        b['estimated_m']=scale*b['distance_model']
        b['residual_m']=b['estimated_m']-b['length_m']
        b['relative_error_percent']=100*b['residual_m']/b['length_m']
    warnings=[]
    if not any(b.get('role')=='check' for b in measurements):
        warnings.append('No held-out check distance: accuracy is not independently verified.')
    if any(abs(b['relative_error_percent'])>1 for b in measurements):
        warnings.append('Distance disagreement exceeds 1%; inspect observations and reconstruction.')
    return {'version':VERSION,'model_fingerprint':model.signature,'meters_per_model_unit':scale,
            'points':points,'distances':measurements,'warnings':warnings,
            'thresholds':{'min_angle_deg':min_angle_deg,'max_error_px':max_error_px},
            'method':'fixed-camera robust reprojection triangulation; weighted uniform scale'}


def export_colmap(model, output, meters_per_unit, unit='m'):
    if unit not in ('m','mm'):
        raise ValueError('Output unit must be m or mm.')
    multiplier=positive(meters_per_unit)*(1000 if unit=='mm' else 1)
    output=Path(output)
    if output.exists():
        raise ValueError('Output directory already exists; choose a new directory.')
    if fingerprint(model.folder)!=model.signature:
        raise ValueError('Source model changed; reload and recompute.')
    # Deep copy via official text I/O, preserving tracks, calibrations and rig semantics.
    with tempfile.TemporaryDirectory() as d:
        model.rec.write_text(d)
        rec=pycolmap.Reconstruction(d)
    rec.transform(pycolmap.Sim3d(multiplier,pycolmap.Rotation3d(),np.zeros(3)))
    output.mkdir(parents=True)
    rec.write_text(str(output))
    (output/'scale_metadata.json').write_text(json.dumps({'unit':unit,
        'multiplier':multiplier,'source_fingerprint':model.signature},indent=2))


def _numbers(text, count):
    values=np.fromstring(text or '',sep=' ')
    if len(values)!=count or not np.all(np.isfinite(values)):
        raise ValueError('Unsupported or invalid XML transform.')
    return values


def _xml_component(chunk):
    """Accept one shared camera coordinate system, not disconnected components."""
    frames=chunk.find('frames')
    if frames is not None and len(frames):
        raise ValueError('Frame/rig XML is not supported.')
    container=chunk.find('components')
    components=container.findall('component') if container is not None else []
    if len(components)>1:
        raise ValueError('Multiple components are not supported; export one aligned component.')
    if not components: return None
    component=components[0]; component_id=component.get('id')
    if component_id is None:
        raise ValueError('XML component is missing its id.')
    if container.get('active_id') not in (None,component_id):
        raise ValueError('XML active component does not match the single component.')
    # A separate component transform would require a different coordinate conversion.
    if component.find('transform') is not None:
        raise ValueError('Component-specific transforms are not supported.')
    return component


def _xml_camera_names(model,cameras):
    """Resolve exact names first, then unique stems / Metashape numeric suffixes.

    No order/id guessing or case folding. The full pose constellation and camera
    rotations must still agree with COLMAP before any output is written.
    """
    def basename(name): return Path(name.replace('\\','/')).name
    def stem(name):
        p=Path(name)
        return p.stem if p.suffix.lower() in ('.jpg','.jpeg','.png','.tif','.tiff','.bmp','.exr') else name
    names={}
    for name in model.images:
        base=basename(name)
        if base in names: raise ValueError('Duplicate image basenames; XML correspondence is ambiguous.')
        names[base]=name
    labels=[basename(c.get('label','')) for c in cameras]
    if any(not label for label in labels) or len(set(labels))!=len(labels):
        raise ValueError('XML camera labels are empty or duplicated.')
    mapping={label:names[label] for label in labels if label in names}
    modes={label:'exact' for label in mapping}; used=set(mapping.values())
    for label in labels:
        if label in mapping: continue
        wanted=stem(label)
        matches=[name for base,name in names.items() if stem(base)==wanted]
        mode='stem'
        if not matches:
            matches=[name for base,name in names.items() if re.fullmatch(re.escape(wanted)+r'_\d+',stem(base))]
            mode='numeric_suffix'
        if len(matches)!=1 or matches[0] in used:
            raise ValueError(f'XML/COLMAP name mismatch or ambiguous mapping: {label}')
        mapping[label]=matches[0]; modes[label]=mode; used.add(matches[0])
    return mapping,modes


def _xml_orientation_check(model,chunk,cams,lookup,world_rotation):
    """Validate poses allowing a tightly bounded shared rectified sensor frame.

    Metashape SIMPLE_PINHOLE exports can use a slightly tilted camera frame.
    Estimate it only from multiple views of the same original sensor; never
    transfer this tilt to the XML used with original photographs.
    """
    def angle(R):
        return float(np.degrees(Rotation.from_matrix(R).magnitude()))
    groups={}
    for camera,tr,matrix in cams:
        label=Path(camera.get('label','').replace('\\','/')).name
        name=lookup[label]
        delta=model.pose(name)[0]@world_rotation@matrix[:3,:3]
        groups.setdefault(camera.get('sensor_id'),[]).append((name,delta))
    raw_errors=[]; checked_errors=[]; adjustments=[]
    for sensor_id,records in groups.items():
        raw=[angle(delta) for name,delta in records]; raw_errors.extend(raw)
        if max(raw)<=.1:
            checked_errors.extend(raw); continue
        U,_,Vt=np.linalg.svd(sum(delta for name,delta in records))
        D=np.eye(3); D[-1,-1]=np.linalg.det(U@Vt)
        shared=U@D@Vt
        residuals=[angle(delta@shared.T) for name,delta in records]
        vector=Rotation.from_matrix(shared).as_rotvec(degrees=True)
        sensor=chunk.find(f"./sensors/sensor[@id='{sensor_id}']")
        calib=sensor.find("calibration[@class='adjusted']")
        original_f=float(calib.findtext('f','nan'))
        output_cameras=[model.camera(name) for name,delta in records]
        # A centered SIMPLE_PINHOLE with unchanged focal length is the supported
        # Metashape rectification case. Lens/roll/large/per-view changes are rejected.
        compatible=(len(records)>=3 and sensor.get('type')=='frame'
                    and calib.get('type')=='frame' and np.isfinite(original_f) and original_f>0
                    and len({camera.camera_id for camera in output_cameras})==1)
        for camera in output_cameras:
            if camera.model_name!='SIMPLE_PINHOLE': compatible=False; break
            f,cx,cy=camera.params
            if abs(f/original_f-1)>1e-3 or abs(cx-camera.width/2)>1e-4 or abs(cy-camera.height/2)>1e-4:
                compatible=False; break
        if (not compatible or angle(shared)>1 or abs(vector[2])>.01 or max(residuals)>.001):
            name=records[int(np.argmax(raw))][0]
            raise ValueError(f'XML/COLMAP camera orientations disagree: sensor {sensor_id}, '
                             f'{name}: raw {max(raw):.6f} degrees, '
                             f'sensor residual {max(residuals):.6f} degrees. '
                             'Use the same alignment and matching export settings.')
        checked_errors.extend(residuals)
        adjustments.append({'sensor_id':sensor_id,'matched_cameras':len(records),
            'rotation_original_to_colmap_camera':shared.tolist(),
            'angle_deg':angle(shared),'max_residual_deg':max(residuals)})
    return {'max_orientation_error_deg':max(checked_errors),
            'max_raw_orientation_error_deg':max(raw_errors),
            'sensor_camera_frame_adjustments':adjustments}


def export_metashape(model, xml_path, output, meters_per_unit):
    """Patch original unreferenced single-chunk XML; preserve sensor calibration.

    Compare camera-center constellations with Umeyama, then scale INTERNAL
    camera translations; global scale/translation are reset to avoid double scale.
    The output is in metres, with original chunk rotation preserved.
    """
    positive(meters_per_unit)
    xml_path=Path(xml_path); output=Path(output)
    if output.exists():
        raise ValueError('XML output already exists; choose a new file.')
    if fingerprint(model.folder)!=model.signature:
        raise ValueError('Source model changed; reload and recompute.')
    tree=ET.parse(xml_path); root=tree.getroot()
    if root.tag!='document':
        raise ValueError('Use Export Cameras → Agisoft XML, not a calibration-only XML.')
    chunks=root.findall('chunk')
    if len(chunks)!=1:
        raise ValueError('Only single-chunk Export Cameras XML is supported.')
    chunk=chunks[0]
    component=_xml_component(chunk)
    if chunk.find('markers') is not None or chunk.find('ground_control') is not None:
        raise ValueError('Referenced/marker XML is outside the local-scale workflow.')
    crs=chunk.find('reference')
    if crs is not None and crs.text and crs.text.strip() and 'LOCAL_CS' not in crs.text:
        raise ValueError('Geographic/projected CRS XML is not supported.')
    aligned=[c for c in chunk.findall('./cameras//camera') if c.find('transform') is not None]
    lookup,name_modes=_xml_camera_names(model,aligned)
    if any(s.get('master_id') is not None for s in chunk.findall('./sensors/sensor')):
        raise ValueError('Multi-camera sensor XML is unsupported in v0.1.')
    cams=[]; X=[]; Y=[]; names=set()
    for camera in chunk.findall('./cameras//camera'):
        tr=camera.find('transform')
        if tr is None:
            continue
        if camera.get('master_id') is not None:
            raise ValueError('Rig cameras are not supported in XML export.')
        component_id=camera.get('component_id')
        if component_id is not None and (component is None or component_id!=component.get('id')):
            raise ValueError('Camera refers to a missing or different XML component.')
        if component is not None and component_id!=component.get('id'):
            raise ValueError('Every aligned camera must belong to the single XML component.')
        label=Path(camera.get('label','').replace('\\','/')).name
        if label not in lookup or label in names:
            raise ValueError(f'XML/COLMAP name mismatch or duplicate: {label}')
        names.add(label)
        sensor=chunk.find(f"./sensors/sensor[@id='{camera.get('sensor_id')}']")
        if sensor is None or sensor.find("calibration[@class='adjusted']") is None:
            raise ValueError('Original XML must contain adjusted sensor calibration.')
        matrix=_numbers(tr.text,16).reshape(4,4)
        if not np.allclose(matrix[3],[0,0,0,1]) or not np.allclose(
            matrix[:3,:3].T@matrix[:3,:3],np.eye(3),atol=1e-5) or np.linalg.det(matrix[:3,:3])<0:
            raise ValueError('Camera transform is not a rigid camera-to-world matrix.')
        cams.append((camera,tr,matrix)); X.append(matrix[:3,3]); Y.append(model.center(lookup[label]))
    if len(cams)!=len(model.images):
        raise ValueError('XML and COLMAP must contain the same set of aligned cameras.')
    if len(cams)<3:
        raise ValueError('XML correspondence requires at least three non-collinear cameras.')
    X=np.array(X); Y=np.array(Y); xc=X-X.mean(0); yc=Y-Y.mean(0)
    if np.linalg.matrix_rank(xc,tol=np.linalg.norm(xc)*1e-8)<2:
        raise ValueError('Camera centers are collinear; XML coordinate correspondence is ambiguous.')
    U,S,Vt=np.linalg.svd(yc.T@xc)
    D=np.eye(3); D[-1,-1]=np.linalg.det(U@Vt)
    R=U@D@Vt
    k=float(np.sum(S*np.diag(D))/np.sum(xc*xc)) # COLMAP units / XML internal unit
    positive(k,'XML coordinate scale')
    errors=np.linalg.norm(yc-k*(xc@R.T),axis=1)
    relative=float(np.sqrt(np.mean(errors**2))/np.sqrt(np.mean(np.sum(yc**2,axis=1))))
    if relative>1e-4:
        raise ValueError(f'XML/COLMAP camera layouts disagree ({relative:.3g}); use same alignment.')
    orientation_report=_xml_orientation_check(model,chunk,cams,lookup,R)
    factor=k*meters_per_unit
    for camera,tr,matrix in cams:
        matrix[:3,3]*=factor
        tr.text=' '.join(f'{v:.17g}' for v in matrix.flat)
        covariance=camera.find('location_covariance')
        if covariance is not None:
            covariance.text=' '.join(f'{v:.17g}' for v in _numbers(covariance.text,9)*factor**2)
        for reference in camera.findall('reference'):
            reference.set('enabled','false')
    global_tr=chunk.find('transform')
    if global_tr is None:
        global_tr=ET.SubElement(chunk,'transform')
    # Keep only rigid rotation, avoiding dependence on original chunk scale.
    rotation=global_tr.find('rotation')
    if rotation is not None:
        gr=_numbers(rotation.text,9).reshape(3,3)
        if not np.allclose(gr.T@gr,np.eye(3),atol=1e-5) or np.linalg.det(gr)<0:
            raise ValueError('Invalid chunk rotation.')
    elif global_tr.text and global_tr.text.strip():
        raise ValueError('Matrix-style chunk transform is unsupported; use current Export Cameras XML.')
    else:
        ET.SubElement(global_tr,'rotation').text='1 0 0 0 1 0 0 0 1'
    for tag,text in [('translation','0 0 0'),('scale','1')]:
        e=global_tr.find(tag)
        if e is None: e=ET.SubElement(global_tr,tag)
        e.text=text
    regions=chunk.findall('region')
    if component is not None: regions.extend(component.findall('region'))
    for region in regions:
        for tag in ('center','size'):
            e=region.find(tag)
            if e is not None: e.text=' '.join(f'{v:.17g}' for v in _numbers(e.text,3)*factor)
    ET.indent(tree,space='  ')
    output.parent.mkdir(parents=True,exist_ok=True)
    tree.write(output,encoding='utf-8',xml_declaration=True)
    return {'unit':'m','xml_internal_multiplier':factor,'colmap_per_xml_unit':k,
            'xml_component_id':component.get('id') if component is not None else None,
            'camera_name_matching':{mode:sum(m==mode for m in name_modes.values()) for mode in ('exact','stem','numeric_suffix')},
            'camera_layout_relative_rms':relative,'matched_cameras':len(cams),**orientation_report,
            'metashape_import_validation':'pending: must verify in installed Standard edition'}


def write_json(path,data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
