#!/usr/bin/env python3
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.run_external_retrieval_eval_v1 import CONTRACT,load_json,load_miracl,load_acord,inspect_jdocqa
def main():
 p=argparse.ArgumentParser(); p.add_argument('--dataset-root',type=Path,required=True); a=p.parse_args(); c=load_json(CONTRACT)
 m=load_miracl(a.dataset_root,c); at=load_acord(a.dataset_root,c,'train'); av=load_acord(a.dataset_root,c,'valid'); j=inspect_jdocqa(a.dataset_root,c)
 print(json.dumps({'miracl':{k:len(m[k]) for k in ('documents','queries','qrels')},'acord_train':{k:len(at[k]) for k in ('documents','queries','qrels')},'acord_valid':{k:len(av[k]) for k in ('documents','queries','qrels')},'jdocqa':j},ensure_ascii=False,sort_keys=True))
if __name__=='__main__': main()
