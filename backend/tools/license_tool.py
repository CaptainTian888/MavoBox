"""Teacher-side license tool. Run from backend/:  uv run python tools/license_tool.py <command>

  init   --out ..\\private\\mavobox_license_private.pem   create the signing key (once!) and print the public key
  issue  --key ..\\private\\mavobox_license_private.pem --name "张三" [--machine XXXXX-XXXXX-XXXXX-XXXXX] [--days 365 | --exp 2027-12-31]
  pub    --key <私钥>                                          打印公钥
  check  --code MAVO1....                                   verify a code against the embedded public key

Keep the private key file secret and backed up: if it is lost you can never issue codes that
existing installs accept; if it leaks anyone can mint codes. Never commit it.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import license as lic  # noqa: E402
from core.license_key import PUBLIC_KEY  # noqa: E402


def main():
    for stream in (sys.stdout, sys.stderr):
        try: stream.reconfigure(encoding='utf-8')
        except Exception: pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    init = sub.add_parser('init'); init.add_argument('--out', required=True)
    issue = sub.add_parser('issue')
    issue.add_argument('--key', required=True); issue.add_argument('--name', required=True)
    issue.add_argument('--machine', default='*', help='学员的机器码；不填表示不绑定电脑')
    group = issue.add_mutually_exclusive_group()
    group.add_argument('--days', type=int); group.add_argument('--exp', help='到期日 YYYY-MM-DD')
    pub = sub.add_parser('pub'); pub.add_argument('--key', required=True)
    check = sub.add_parser('check'); check.add_argument('--code', required=True)
    check.add_argument('--machine', default='*')
    args = parser.parse_args()

    if args.cmd == 'init':
        out = Path(args.out)
        if out.exists():
            sys.exit(f'拒绝覆盖已有私钥：{out}')
        out.parent.mkdir(parents=True, exist_ok=True)
        pem, public = lic.generate_keypair()
        out.write_bytes(pem)
        print('私钥已写入:', out)
        print('公钥(写入 core/license_key.py 的 PUBLIC_KEY):', public)
    elif args.cmd == 'issue':
        private = lic.load_private(Path(args.key).read_bytes())
        exp = args.exp or ((dt.date.today() + dt.timedelta(days=args.days)).isoformat() if args.days else None)
        payload = dict(v=1, name=args.name, machine=args.machine, exp=exp,
                       iat=dt.date.today().isoformat())
        print(lic.sign(payload, private))
        print(f'-- 授权给 {args.name}，绑定: {args.machine}，到期: {exp or "永久"}', file=sys.stderr)
    elif args.cmd == 'pub':
        from cryptography.hazmat.primitives import serialization
        key = lic.load_private(Path(args.key).read_bytes()).public_key()
        print(lic.b64e(key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)))
    else:
        machine = args.machine if args.machine != '*' else lic.machine_code()
        try:
            payload = lic.verify(args.code, PUBLIC_KEY, machine)
        except lic.LicenseError as exc:
            sys.exit(f'无效: {exc}')
        print('有效:', payload)


if __name__ == '__main__':
    main()
