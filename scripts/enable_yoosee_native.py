"""Trusted host command: activate an already-homologated camera in Docker."""

import argparse
from pathlib import Path

from backend.app.camera_identity import valid_camera_id
from backend.app.diagnostics.native_installation import install, rollback


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--camera-id')
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,default=Path('docs/internal/native-av-delivery.md'))
    parser.add_argument('--rollback',action='store_true')
    parser.add_argument('--confirm-interruption',action='store_true')
    args=parser.parse_args()
    if args.rollback:
        rollback(args.directory)
        return
    if not args.confirm_interruption or not valid_camera_id(args.camera_id):
        parser.error('configured camera and explicit stack interruption required')
    install(args.camera_id,args.directory,args.evidence)


if __name__=='__main__': main()
