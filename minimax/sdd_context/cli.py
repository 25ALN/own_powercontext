import argparse
import json
from pathlib import Path

from .core import (
    apply_patch,
    generate_patch,
    import_docs,
    load_config,
    make_context,
    save_state,
)


def main() -> None:
    load_config()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import"); imp.add_argument("root", type=Path)
    ctx = sub.add_parser("context"); ctx.add_argument("task")
    gen = sub.add_parser("generate"); gen.add_argument("task")
    apply = sub.add_parser("apply"); apply.add_argument("diff", type=Path); apply.add_argument("--repo", type=Path, default=Path.cwd())
    state = sub.add_parser("state"); state.add_argument("task"); state.add_argument("--done", action="append", default=[]); state.add_argument("--todo", action="append", default=[]); state.add_argument("--tests", default="not run")
    args = parser.parse_args()
    if args.cmd == "import": print(f"imported={import_docs(args.root)}")
    elif args.cmd == "context": print(json.dumps(make_context(args.task), ensure_ascii=False, indent=2))
    elif args.cmd == "generate": print(generate_patch(make_context(args.task)))
    elif args.cmd == "apply": apply_patch(args.diff.read_text(), args.repo)
    else: print(f"scope_id={save_state(args.task,args.done,args.todo,args.tests)}")


if __name__ == "__main__": main()
