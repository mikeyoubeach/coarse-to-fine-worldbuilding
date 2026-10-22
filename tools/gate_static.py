#!python3
"""Static gate for "one unit = one file" submissions.  Pure standard library.

This checks the things you can check WITHOUT rendering:

  1. the unit file exists and calls the registration entry point
  2. it does not use the APIs you banned (Math.random, own materials, ...)
  3. every `mark('name')` in the source also appears in the built section list
     reported by the page (pass that in with --built) — the "silent empty
     section" check
  4. coverage: every unit id in the data table has a module, and no module is
     an empty stub

It deliberately does NOT check bounds or triangle counts: those need a real
browser. See gate_geometry.js for the in-page half.

MATURITY NOTE — read this before trusting it
--------------------------------------------
An earlier version of this gate advertised "bounds are checked" in its
docstring and never ran a bounds check. Three batches of out-of-bounds work
came back green. That is the single worst failure mode in this whole
pipeline, so:

    RUN `python gate_static.py --selftest`

before you rely on it. --selftest builds throwaway fixtures that MUST fail,
runs every check against them, and asserts each one actually fires. If a check
passes its must-fail fixture, this script exits non-zero and tells you which
check is dead.

Exit code: 0 = PASS, 1 = FAIL.
"""
import argparse
import json
import pathlib
import re
import sys
import tempfile

# ---------------------------------------------------------------- config
# Adapt these four lines to your project and you are done.

ENTRY = "registerUnit"                 # APP.registerUnit(N, function (b, ctx) {...})
FILE_PATTERN = r"unit_(\d+)\.js$"      # one file per unit, id in the name
UNIT_DIR = "src/units"                 # where those files live
DATA_TABLE = "src/units/bounds.json"   # { "<id>": {"polygon": [[x,z], ...]}, ... }

# Things you banned. Each is (name, regex, message).
BANNED = [
    ("Math.random", r"Math\.random\s*\(",
     "用了 Math.random()：结果不可复现，请改用 ctx.rnd"),
    ("own-material", r"new\s+(?:THREE|T)\.\w*Material",
     "自己建了材质：几何请全部推给 b"),
    ("own-mesh", r"new\s+(?:THREE|T)\.Mesh\b",
     "自己建了 Mesh：几何请全部推给 b"),
    ("hardcoded-y", r"\by\s*:\s*0\s*[,}]",
     "写死了 y: 0：地形有起伏，请用 ctx.ground(x, z)"),
]

STUB_MARKERS = ("your work goes here", "TODO", "TODO:")


# ---------------------------------------------------------------- checks

def check_hygiene(path):
    """Returns (problems, warnings)."""
    problems, warns = [], []
    if not path.exists():
        return ["文件不存在：%s" % path], warns
    src = path.read_text(encoding="utf-8")

    if ENTRY not in src:
        problems.append("没有调用 %s" % ENTRY)
    for name, pat, msg in BANNED:
        if re.search(pat, src):
            problems.append("[%s] %s" % (name, msg))

    # A registered function whose body is empty.
    if re.search(ENTRY + r"\s*\(\s*\d+\s*,\s*function\s*\([^)]*\)\s*\{\s*\}\s*\)", src):
        problems.append("注册了但函数体是空的（stub）")

    if not re.search(r"\bmark\s*\(", src):
        warns.append("一段都没有 mark()：出了空段也查不出来")
    return problems, warns


def declared_sections(path):
    """Section names the source says it builds, in first-seen order."""
    if not path.exists():
        return []
    src = path.read_text(encoding="utf-8")
    seen = []
    for name in re.findall(r"\.mark\(\s*['\"]([^'\"]+)['\"]\s*\)", src):
        if name not in seen:
            seen.append(name)
    return seen


def check_empty_sections(path, built_names):
    """The silent-failure check.

    Why this needs its own function: in the reference project the geometry
    helper only registers a section from inside push(). A section that built
    nothing therefore does not show up as `tris: 0` — it vanishes from the
    list entirely. Build succeeds, gate is green, report says "fence done".
    Only comparing declared-vs-built finds it.
    """
    if built_names is None:
        return None                      # not measured; caller prints a warning
    built = set(built_names)
    return [n for n in declared_sections(path) if n not in built]


def check_coverage(unit_dir, data_table):
    have = {}
    if unit_dir.is_dir():
        for p in unit_dir.glob("*.js"):
            m = re.search(FILE_PATTERN, p.name)
            if m:
                have[int(m.group(1))] = p
    want = []
    dt = pathlib.Path(data_table)
    if dt.exists():
        try:
            want = sorted(int(k) for k in json.loads(dt.read_text(encoding="utf-8")))
        except Exception as exc:
            return False, ["数据表读不出来：%s" % exc]
    if not want:
        # A missing data table makes this check vacuously true — which is the
        # exact failure mode this whole repo warns about. Refuse loudly.
        return False, ["没有数据表（%s）或它是空的：覆盖检查无从下手，"
                       "不能当成 PASS" % data_table]

    missing = [i for i in want if i not in have]
    stubs = []
    for i, p in have.items():
        txt = p.read_text(encoding="utf-8")
        if any(mk in txt for mk in STUB_MARKERS):
            stubs.append(i)

    problems = []
    if missing:
        problems.append("缺模块 %d 个：%s（这些在总览里是灰模/空地，不是完成品）"
                        % (len(missing), ", ".join(str(i) for i in missing)))
    if stubs:
        problems.append("空壳模块 %d 个：%s" % (len(stubs), ", ".join(str(i) for i in stubs)))
    return (not problems), problems


# ---------------------------------------------------------------- selftest

def selftest():
    """Build fixtures that MUST fail. A gate nobody has ever seen fail is
    indistinguishable from a gate that always prints PASS."""
    failures = []
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        units = root / "src" / "units"
        units.mkdir(parents=True)

        def probe(label, body, expect_problem_substr):
            p = units / "unit_99.js"
            p.write_text(body, encoding="utf-8")
            probs, _ = check_hygiene(p)
            hit = any(expect_problem_substr in x for x in probs)
            if not hit:
                failures.append("卫生检查没抓到 %s" % label)
            p.unlink()

        good = "APP.registerUnit(99, function (b, ctx) { b.mark('ok'); b.box(1,1,1,{}); });\n"
        p = units / "unit_99.js"
        p.write_text(good, encoding="utf-8")
        probs, _ = check_hygiene(p)
        if probs:
            failures.append("正常代码被误判：%s" % probs)
        p.unlink()

        probe("Math.random", good.replace("b.box(1,1,1,{});", "b.box(1,Math.random());"),
              "Math.random")
        probe("自建材质", good.replace("b.box(1,1,1,{});", "new THREE.MeshBasicMaterial();"),
              "材质")
        probe("缺入口", good.replace("APP.registerUnit", "APP.nothing"),
              ENTRY)
        probe("空函数体", "APP.registerUnit(99, function (b, ctx) { });", "空的")
        probe("写死 y", good.replace("{}", "{y:0}"), "写死")

        # empty-section check
        p = units / "unit_99.js"
        p.write_text("APP.registerUnit(99, function(b,c){ b.mark('fence'); });", encoding="utf-8")
        miss = check_empty_sections(p, ["other"])
        if miss != ["fence"]:
            failures.append("空段检查没抓到未建成的段：%r" % (miss,))
        if check_empty_sections(p, ["fence"]) != []:
            failures.append("空段检查把建成的段误判成空")
        p.unlink()

        # coverage check
        (units / "bounds.json").write_text(json.dumps({"1": {}, "2": {}, "3": {}}), encoding="utf-8")
        (units / "unit_1.js").write_text(good.replace("99", "1"), encoding="utf-8")
        (units / "unit_2.js").write_text("// your work goes here\n", encoding="utf-8")
        ok, probs = check_coverage(units, units / "bounds.json")
        if ok:
            failures.append("覆盖检查对缺模块+空壳返回了 PASS")
        joined = " ".join(probs)
        if "缺模块" not in joined:
            failures.append("覆盖检查没报缺模块：%s" % joined)
        if "空壳" not in joined:
            failures.append("覆盖检查没报空壳：%s" % joined)

    if failures:
        print("SELFTEST FAILED — 以下检查是死的：")
        for f in failures:
            print("  x " + f)
        return 1
    print("SELFTEST PASS — 每条检查都在它的必须失败夹具上失败了。")
    return 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="静态闸：一个单元一个文件")
    ap.add_argument("ids", nargs="*", type=int, help="要检查的单元 id")
    ap.add_argument("--all", action="store_true", help="检查所有已存在的单元")
    ap.add_argument("--coverage", action="store_true", help="只跑覆盖检查")
    ap.add_argument("--built", help="JSON 文件：{\"<id>\": [\"段名\", ...]}，由页面导出")
    ap.add_argument("--dir", default=UNIT_DIR)
    ap.add_argument("--table", default=DATA_TABLE)
    ap.add_argument("--selftest", action="store_true", help="验证本闸真的会 FAIL")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    unit_dir = pathlib.Path(a.dir)
    ok = True
    built = {}
    if a.built:
        built = json.loads(pathlib.Path(a.built).read_text(encoding="utf-8"))
    elif not (a.coverage and not a.ids):
        print("! 没给 --built，空段检查跳过（这一项才抓得住静默失败）")

    if a.coverage and not a.ids:
        ok_c, probs = check_coverage(unit_dir, a.table)
        print("=" * 60)
        for p in probs:
            print("  x " + p)
        if ok_c:
            print("  . 覆盖完整")
        return 0 if ok_c else 1

    ids = a.ids
    if a.all or not ids:
        ids = sorted(int(m.group(1)) for p in unit_dir.glob("*.js")
                     if (m := re.search(FILE_PATTERN, p.name)))

    for uid in ids:
        path = unit_dir / ("unit_%d.js" % uid)
        print("=" * 60)
        print(path.name)
        probs, warns = check_hygiene(path)
        for w in warns:
            print("  ! " + w)
        for p in probs:
            ok = False
            print("  x " + p)

        missing = check_empty_sections(path, built.get(str(uid)))
        if missing is None:
            print("  ? 空段：未实测")
        elif missing:
            ok = False
            print("  x 空段 %d 个：%s（源码 mark 了，但一个三角形都没建出来）"
                  % (len(missing), ", ".join(missing)))
        else:
            print("  . 分段 %d 个，全部非空" % len(declared_sections(path)))

        if not probs and not missing:
            print("  OK")

    if a.coverage:
        ok_c, probs = check_coverage(unit_dir, a.table)
        print("=" * 60)
        for p in probs:
            ok = False
            print("  x " + p)

    print("=" * 60)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
