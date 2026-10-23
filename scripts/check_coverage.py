"""Enforce separate statement and branch thresholds."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('report',nargs='?',default='coverage.json')
    parser.add_argument('--statements',type=float,default=90.)
    parser.add_argument('--branches',type=float,default=80.)
    args=parser.parse_args()
    totals=json.loads(Path(args.report).read_text(encoding='utf-8'))['totals']
    statements=100*totals['covered_lines']/max(totals['num_statements'],1)
    branches=100*totals['covered_branches']/max(totals['num_branches'],1)
    print(f'Statements {statements:.2f}% (required {args.statements:.1f}%), branches {branches:.2f}% (required {args.branches:.1f}%)')
    return int(statements<args.statements or branches<args.branches)


if __name__=='__main__':
    raise SystemExit(main())
