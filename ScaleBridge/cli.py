"""Recompute a GUI session and export scaled cameras without opening Qt."""
import argparse
import json
from pathlib import Path
from core import Model, solve, export_colmap, export_metashape, write_json

def main():
    p=argparse.ArgumentParser()
    p.add_argument('session',type=Path)
    p.add_argument('--colmap-output',type=Path)
    p.add_argument('--xml-output',type=Path)
    p.add_argument('--unit',choices=['m','mm'],default='m')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args(); data=json.loads(args.session.read_text())
    for key,value in data['paths'].items():
        if value and not Path(value).is_absolute():data['paths'][key]=str((args.session.parent/value).resolve())
    model=Model(data['paths']['model'])
    if model.signature!=data['model_fingerprint']:raise ValueError('Model changed since session was saved.')
    report=solve(model,data['observations'],data['bars'],data.get('min_angle_deg',1),data.get('max_error_px',10))
    if args.colmap_output:export_colmap(model,args.colmap_output,report['meters_per_model_unit'],args.unit)
    if args.xml_output:
        report['metashape_export']=export_metashape(model,data['paths']['xml'],args.xml_output,report['meters_per_model_unit'])
    write_json(args.report,report)
    print(f"Scale = {report['meters_per_model_unit']:.12g} m/model-unit")
if __name__=='__main__':main()
