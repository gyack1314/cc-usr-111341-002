"""命令行入口：资料检查、未回应敏感性分析、逐人可追问记录。

不带子命令时保留原有检查行为；分析结果以 JSON 输出，口径版本与摘要
随报告回带，便于发布稿引用。
"""
import argparse
import json

from .io import load, DataError
from .domain import inspect_tables
from .analysis import run_analysis
from .calibration import DEFAULT_CALIBRATION


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_inspect(args):
    _print(inspect_tables(load(args.manifest)))


def _cmd_analyze(args):
    calibration = DEFAULT_CALIBRATION
    report = run_analysis(load(args.manifest), manifest_path=args.manifest,
                          calibration=calibration)
    _print(report)


def _cmd_person(args):
    report = run_analysis(load(args.manifest), manifest_path=args.manifest)
    rows = [row for row in report['person_audit']
            if args.person is None or row['id'] == args.person]
    if args.person and not rows:
        raise SystemExit(f'名单中没有 {args.person}')
    _print(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description='数字服务覆盖率未回应敏感性分析')
    sub = parser.add_subparsers(dest='command')

    p_inspect = sub.add_parser('inspect', help='检查资料身份与完整性（默认行为）')
    p_inspect.add_argument('manifest')
    p_inspect.set_defaults(func=_cmd_inspect)

    p_analyze = sub.add_parser('analyze', help='输出未回应敏感性联合分析报告')
    p_analyze.add_argument('manifest')
    p_analyze.set_defaults(func=_cmd_analyze)

    p_person = sub.add_parser('person', help='输出逐人状态、联系历史与权重贡献')
    p_person.add_argument('manifest')
    p_person.add_argument('--id', dest='person', default=None)
    p_person.set_defaults(func=_cmd_person)

    # 无子命令：兼容旧调用 python -m coverage_data.cli <manifest>
    argv = list(argv) if argv is not None else None
    if argv is None:
        import sys
        argv = sys.argv[1:]
    if argv and not argv[0].startswith('-') and argv[0] not in ('inspect', 'analyze', 'person'):
        argv = ['inspect'] + argv
    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return
    try:
        args.func(args)
    except (DataError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
