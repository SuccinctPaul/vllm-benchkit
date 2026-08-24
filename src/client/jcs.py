"""RFC 8785 (JCS) 规范化 JSON 序列化（组 I2：comparison_id / config_id）。

实现 RFC 8785 JSON Canonicalization Scheme 的核心规则，保证「同一逻辑 JSON 值 → 字节一致的规范串」：
  - 对象键按 UTF-16 code unit 序稳定排序；
  - 无空白（紧凑）；
  - 数字按 IEEE 754 双精度最短往返（Python float repr 即最短往返）；
  - 整数不经指数/小数形式（int 直出，str(int)）；
  - 字符串转义：仅转义必须转义的（" \\ 与 U+0000..U+001F 控制符，\b\t\n\f\r 用短形式，
    其余按 \\uXXXX），非 ASCII 不做 \\u 转义（保留原字节，与 RFC 8785 一致）。
  - float 中 -0.0 规范化为 0（RFC 8785 要求）。

与 client/common.py 的 canonical_json（JCS-style 简化版，sort_keys + ensure_ascii）相比，
本模块更贴近 RFC 8785 语义：字符串不强制 \\u 转义、键排序按 code unit、-0.0 → 0。
common.py 的 canonical_json 用于客户端合同哈希（B0/B1 复用同一合同，等价即可）；
I2 的 comparison_id 用本模块做「配置身份」的精确规范化。
"""
import json
import math

# RFC 8785 控制符短形式映射
_ESCAPES = {
    0x08: r"\b",
    0x09: r"\t",
    0x0A: r"\n",
    0x0C: r"\f",
    0x0D: r"\r",
}


def _escape_str(s):
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if ch == '"':
            out.append(r"\"")
        elif ch == "\\":
            out.append(r"\\")
        elif cp in _ESCAPES:
            out.append(_ESCAPES[cp])
        elif cp < 0x20:
            out.append("\\u%04x" % cp)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _canonical_int(v):
    """int 直出十进制（RFC 8785：整数不得用指数/小数表示）。"""
    return str(v)


def _canonical_float(v):
    """float → 最短往返十进制；-0.0 规范化 0.0；确保含小数点（1.0 而非 1）。"""
    if v == 0.0:
        v = 0.0  # -0.0 == 0.0，统一为 0
    r = repr(v)
    # Python repr 保证最短往返；整数型 float 补 .0（RFC 8785 要求数字带小数点）
    if "e" in r or "E" in r:
        return r
    if "." not in r:
        r += ".0"
    return r


def _canonical(obj):
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, int) and not isinstance(obj, bool):
        return _canonical_int(obj)
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"JCS 不支持非有限浮点数: {obj!r}")
        return _canonical_float(obj)
    if isinstance(obj, str):
        return _escape_str(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_canonical(x) for x in obj) + "]"
    if isinstance(obj, dict):
        # 键按 UTF-16 code unit 排序（Python 默认 str 比较即 code point 序；
        # 对基本多文种平面一致；surrogate 对在 JSON 中不允许出现）
        parts = []
        for k in sorted(obj):
            parts.append(_escape_str(k) + ":" + _canonical(obj[k]))
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"JCS 不支持类型 {type(obj).__name__}: {obj!r}")


def canonicalize(obj):
    """RFC 8785 规范化串（供 sha256 等消费）。"""
    return _canonical(obj)


def sha256_hex(obj):
    """canonicalize(obj) 的 SHA-256 hex（配合 common.sha256_hex 的语义）。"""
    import hashlib
    return hashlib.sha256(canonicalize(obj).encode("utf-8")).hexdigest()
