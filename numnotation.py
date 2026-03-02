#!/usr/bin/env python3
"""
NumNotation v0.2 → LilyPond 转换器

时值规则（与简谱一致）：
  基本音符          = 四分音符 (4)
  后跟独立 token -  = 每个延长一拍
  紧跟 _            = 缩短一半（_ → 8, __ → 16, ___ → 32）
  紧跟 .            = 附点
"""

import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

# ─────────────────────────────────────────────
# 音名映射
# ─────────────────────────────────────────────

NOTE_NAMES = {
    "C":"c","Db":"des","D":"d","Eb":"ees","E":"e",
    "F":"f","Gb":"ges","G":"g","Ab":"aes","A":"a",
    "Bb":"bes","B":"b",
    "C#":"cis","D#":"dis","F#":"fis","G#":"gis","A#":"ais",
}
MAJOR_IV = [0,2,4,5,7,9,11]
MINOR_IV = [0,2,3,5,7,8,10]
NOTE_ST  = {"C":0,"C#":1,"Db":1,"D":2,"D#":3,"Eb":3,"E":4,
            "F":5,"F#":6,"Gb":6,"G":7,"G#":8,"Ab":8,"A":9,"A#":10,"Bb":10,"B":11}
ST_SHARP = ["c","cis","d","dis","e","f","fis","g","gis","a","ais","b"]
ST_FLAT  = ["c","des","d","ees","e","f","ges","g","aes","a","bes","b"]
FLAT_KEYS = {"F","Bb","Eb","Ab","Db","Gb"}


def lily_key_st(lily: str) -> int:
    rev = {v:k for k,v in NOTE_NAMES.items()}
    return NOTE_ST.get(rev.get(lily,"C"), 0)


def use_flat(lily: str) -> bool:
    rev = {v:k for k,v in NOTE_NAMES.items()}
    return rev.get(lily,"C") in FLAT_KEYS


def degree2lily(deg:int, acc:str, oct_mod:int, key_st:int, mode:str, flat:bool, guitar:bool=False) -> str:
    scale = MINOR_IV if mode=="minor" else MAJOR_IV
    # 音级对应的半音值（模12）
    degree_st = (key_st + scale[(deg-1)%7]) % 12
    if acc=="#": note_st=(degree_st+1)%12
    elif acc=="b": note_st=(degree_st-1)%12
    else: note_st = degree_st
    note = (ST_FLAT if flat else ST_SHARP)[note_st]
    # 八度计算：
    # oct_mod=0 表示"自然位置"——与主音在同一个八度内
    # 若该音级的半音值 < 主音半音值，说明它在主音上方，需要额外+1个八度
    # 例：G大调 key_st=7，4=C(0) < 7，所以4在 c'' 而非 c'
    natural_shift = 1 if degree_st < key_st else 0
    # treble_8 谱号只影响五线谱的视觉显示，TAB 直接用绝对音高。
    # 吉他实际音域比钢琴低一个八度：oct_mod=0 → c（C3），钢琴 oct_mod=0 → c'（C4）
    base_oct = 0 if guitar else 1
    abs_oct = oct_mod + base_oct + natural_shift
    if abs_oct > 0:   ov = "'" * abs_oct
    elif abs_oct < 0: ov = "," * (-abs_oct)
    else:             ov = ""
    return note+ov

# ─────────────────────────────────────────────
# 时值计算
# ─────────────────────────────────────────────
# 用 Fraction 精确表示时值（以四分音符为1）

def compute_duration(underscores:int, dots:int, extra_beats:int) -> str:
    """
    underscores: 减时线数量  0→四分, 1→八分, 2→十六分, 3→三十二分
    dots:        附点数量
    extra_beats: 增时线数量（每个+1拍）
    返回 LilyPond 时值字符串，如 "4" "2" "8" "4." "2." "1" 等
    """
    # 基础时值（以四分音符=1为单位）
    base_quarters = Fraction(1, 2**underscores)   # 0→1, 1→1/2, 2→1/4, 3→1/8

    if extra_beats > 0:
        # 增时线：每个 - 延长一个四分音符
        total = base_quarters + Fraction(extra_beats)
        # 化为最简附点表示
        # 尝试用附点表示
        lily_dur = quarters_to_lily(total)
        return lily_dur
    else:
        # 附点
        total = base_quarters
        dot_add = base_quarters
        for _ in range(dots):
            dot_add = dot_add / 2
            total += dot_add
        lily_dur = quarters_to_lily(total)
        return lily_dur


def quarters_to_lily(q: Fraction) -> str:
    """将以四分音符为1的时值转换为LilyPond时值字符串"""
    # 常用时值映射
    TABLE = {
        Fraction(4):    "1",
        Fraction(3):    "2.",
        Fraction(2):    "2",
        Fraction(3,2):  "4.",
        Fraction(1):    "4",
        Fraction(3,4):  "8.",
        Fraction(1,2):  "8",
        Fraction(3,8):  "16.",
        Fraction(1,4):  "16",
        Fraction(3,16): "32.",
        Fraction(1,8):  "32",
        Fraction(7,4):  "1..",   # 双附点二分近似（实际7/4）
        Fraction(7,8):  "2..",
    }
    if q in TABLE:
        return TABLE[q]
    # fallback：找最近的整数分音符
    # 尝试 den=1,2,4,8,16,32
    for base_den in [1,2,4,8,16,32]:
        base = Fraction(4, base_den)  # 4/den 四分音符数
        # 单附点
        if base * Fraction(3,2) == q:
            return f"{base_den}."
        # 双附点
        if base * Fraction(7,4) == q:
            return f"{base_den}.."
    # 最后fallback
    return "4"


# ─────────────────────────────────────────────
# Tokenizer
# ─────────────────────────────────────────────

@dataclass
class NoteToken:
    """解析后的单个音符/和弦/休止符"""
    kind: str           # "note" | "rest" | "chord" | "bar" | "directive"
    # note/rest/chord 公用
    underscores: int = 0
    dots: int = 0
    extra_beats: int = 0   # 增时线数量（由后续 - token 填入）
    # note
    degree: int = 0
    acc: str = ""
    oct_mod: int = 0
    # chord
    chord_notes: list = None  # list of (degree, acc, oct_mod)
    # annotations
    rh: str = ""        # pima
    lh: str = ""        # 1-4
    string_no: str = ""
    piano_finger: str = ""
    tie: bool = False
    slur_start: bool = False   # ( opens slur on this note
    slur_end: bool = False     # ) closes slur on this note
    decorations: list = None   # lily snippets prepended
    # bar / directive
    value: str = ""

    def __post_init__(self):
        if self.chord_notes is None: self.chord_notes = []
        if self.decorations is None: self.decorations = []


def tokenize_line(s: str, key_st:int, mode:str, flat:bool) -> list[NoteToken]:
    """将一行乐谱文本解析为 NoteToken 列表"""
    tokens: list[NoteToken] = []
    pos = 0
    pending_decorations: list[str] = []
    pending_slur_start: bool = False

    def push_deco(d): pending_decorations.append(d)
    def take_decos():
        d = pending_decorations[:]
        pending_decorations.clear()
        return d

    while pos < len(s):
        c = s[pos]

        if c in " \t": pos+=1; continue
        if c == "%": break

        # 增时线：独立 token，附加到上一个音符
        if c == "-" and (pos==0 or s[pos-1] in " \t|"):
            if tokens:
                last = tokens[-1]
                if last.kind in ("note","rest","chord"):
                    last.extra_beats += 1
                    pos+=1; continue
            # 没有前置音符则当连字符忽略
            pos+=1; continue

        # 小节线
        m = re.match(r'(\|\.|\|::|:\||\|:|\|\||\|)', s[pos:])
        if m:
            bar_map = {"|":"| ","|]":'\\bar "|." ',"||":'\\bar "||" ',
                       "|:":"\\repeat volta 2 { ",":|":"} ",
                       "|::":"} \\repeat volta 2 { "}
            t = NoteToken(kind="bar", value=bar_map.get(m.group(1), "| ").rstrip())
            t.decorations = take_decos()
            tokens.append(t)
            pos+=len(m.group(0)); continue

        # 力度/装饰 !xxx!
        m = re.match(r'!([\w]+)!', s[pos:])
        if m:
            dmap = {"p":"\\p","pp":"\\pp","mp":"\\mp","mf":"\\mf","f":"\\f",
                    "ff":"\\ff","fff":"\\fff","cresc":"\\<","decresc":"\\>",
                    "ped":"\\sustainOn","pup":"\\sustainOff",
                    "harm":"\\flageolet","pizz":"\\pizz"}
            push_deco(dmap.get(m.group(1), f'\\markup{{\\small {m.group(1)}}}'))
            pos+=len(m.group(0)); continue

        # 把位 P:CVII
        m = re.match(r'P:([BC]?)([IVX]+)', s[pos:])
        if m:
            push_deco(f'^\\markup{{\\small \\bold {m.group(1)+m.group(2)}}}')
            pos+=len(m.group(0)); continue

        # 三连音 (3 (5 ...（开始花括号）
        m = re.match(r'\((\d+)(?=\s)', s[pos:])
        if m:
            n = int(m.group(1))
            t = NoteToken(kind="directive", value=f"\\tuplet {n}/2 {{")
            t.decorations = take_decos()
            tokens.append(t)
            pos+=len(m.group(0)); continue

        # 圆滑线：( 标记下一个音符开始slur，) 标记上一个音符结束slur
        if c=="(":
            pending_slur_start = True
            pos+=1; continue
        if c==")":
            # 标记最近一个note/chord/rest
            for tok in reversed(tokens):
                if tok.kind in ("note","chord","rest"):
                    tok.slur_end = True
                    break
            pos+=1; continue

        # 波音 ~
        if c=="~" and (pos+1 < len(s) and s[pos+1] in "1234567"):
            push_deco("\\mordent"); pos+=1; continue

        # 颤音 tr
        if s[pos:pos+2]=="tr" and (pos+2<len(s) and s[pos+2] in "1234567"):
            push_deco("\\trill"); pos+=2; continue

        # 和弦 [音符列表]_*.*
        m = re.match(r"\[([^\]]+)\](_*)(\.*)", s[pos:])
        if m and re.search(r'[1-7]', m.group(1)):
            inner   = m.group(1)
            uscores = len(m.group(2))
            ndots   = len(m.group(3))
            chord_notes = _parse_chord_inner(inner)
            t = NoteToken(kind="chord", underscores=uscores, dots=ndots,
                          chord_notes=chord_notes)
            t.decorations = take_decos()
            if pending_slur_start:
                t.slur_start = True
                pending_slur_start = False
            tokens.append(t)
            pos+=len(m.group(0)); continue

        # 音符
        m = re.match(
            r"([#b=]?)([1-7])([,']*)"     # acc degree octave
            r"(_*)(\.*)"                  # underscores dots
            r"(~?)"                       # tie
            r"(\[([pima])\])?"            # right hand
            r"(\{([1-4])\})?"             # left hand
            r"(\(s(\d)\))?"              # string
            r"(<(\d)>)?",               # piano finger
            s[pos:]
        )
        if m:
            acc     = m.group(1)
            deg     = int(m.group(2))
            octs    = m.group(3)
            uscores = len(m.group(4))
            ndots   = len(m.group(5))
            tie     = bool(m.group(6))
            rh      = m.group(8) or ""
            lh      = m.group(10) or ""
            sno     = m.group(12) or ""
            pf      = m.group(14) or ""
            oct_mod = octs.count("'")-octs.count(",")
            t = NoteToken(kind="note", degree=deg, acc=acc, oct_mod=oct_mod,
                          underscores=uscores, dots=ndots, tie=tie,
                          rh=rh, lh=lh, string_no=sno, piano_finger=pf)
            t.decorations = take_decos()
            if pending_slur_start:
                t.slur_start = True
                pending_slur_start = False
            tokens.append(t)
            pos+=len(m.group(0)); continue

        # 休止符
        m = re.match(r"0(_*)(\.*)", s[pos:])
        if m:
            t = NoteToken(kind="rest", underscores=len(m.group(1)), dots=len(m.group(2)))
            t.decorations = take_decos()
            tokens.append(t)
            pos+=len(m.group(0)); continue

        pos+=1

    return tokens


def _parse_chord_inner(s:str) -> list:
    notes=[]
    for part in s.split():
        m=re.match(r"([#b=]?)([1-7])([,']*)", part.strip())
        if m:
            oct_mod=m.group(3).count("'")-m.group(3).count(",")
            notes.append((int(m.group(2)), m.group(1), oct_mod))
    return notes


# ─────────────────────────────────────────────
# Token → LilyPond string
# ─────────────────────────────────────────────

# Dynamics that must be postfixed to the note (not free-standing)
_POSTFIX_DYNAMICS = {"\\p","\\pp","\\mp","\\mf","\\f","\\ff","\\fff","\\<","\\>"}

def token_to_lily(t: NoteToken, key_st:int, mode:str, flat:bool, guitar:bool=False) -> str:
    pre_decos  = [d for d in t.decorations if d not in _POSTFIX_DYNAMICS]
    post_decos = [d for d in t.decorations if d in _POSTFIX_DYNAMICS]
    pre  = " ".join(pre_decos)+" "  if pre_decos  else ""
    post = "".join(post_decos)      if post_decos  else ""

    if t.kind == "bar":
        return pre + t.value

    if t.kind == "directive":
        return pre + t.value

    dur = compute_duration(t.underscores, t.dots, t.extra_beats)

    if t.kind == "rest":
        return pre + f"r{dur}" + post

    if t.kind == "chord":
        notes = [degree2lily(d,a,o,key_st,mode,flat,guitar=guitar) for d,a,o in t.chord_notes]
        slur_open  = "(" if t.slur_start else ""
        slur_close = ")" if t.slur_end   else ""
        return pre + f"<{' '.join(notes)}>{dur}" + slur_open + post + slur_close

    if t.kind == "note":
        note = degree2lily(t.degree, t.acc, t.oct_mod, key_st, mode, flat, guitar=guitar)
        slur_open  = "(" if t.slur_start else ""
        slur_close = ")" if t.slur_end   else ""
        result = pre + note + dur + slur_open + post + slur_close
        if t.rh:     result += f'-\\markup{{\\small \\italic {t.rh}}}'
        if t.lh:     result += f'-{t.lh}'
        if t.string_no: result += f'^\\markup{{\\circle {{\\small {t.string_no}}}}}'
        if t.piano_finger: result += f'-{t.piano_finger}'
        if t.tie:    result += "~"
        return result

    return ""


# ─────────────────────────────────────────────
# 解析器
# ─────────────────────────────────────────────

@dataclass
class Header:
    index: int = 1
    title: str = ""
    composer: str = ""
    meter: str = "4/4"
    tempo: str = "1/4=120"
    tempo_text: str = ""
    key_note: str = "c"
    key_mode: str = "major"
    instrument: str = "guitar"

@dataclass
class VoiceDef:
    name: str
    stem: str = ""
    clef: str = "treble"


class NumNotationParser:
    def __init__(self, source:str):
        self.lines = source.splitlines()
        self.header = Header()
        self.voices: dict[str,VoiceDef] = {}
        self.voice_music: dict[str,list[NoteToken]] = {}
        self.voice_order: list[str] = []
        self.current_voice: Optional[str] = None

    def parse(self):
        in_body = False
        for raw in self.lines:
            line = raw.strip()
            if not line or line.startswith("%"): continue
            if line.startswith("V:"):
                self._voice_def(line); in_body=True; continue
            if not in_body and re.match(r'^[A-Z]:', line):
                self._field(line)
                if line[0]=="K": in_body=True
                continue
            if line.startswith("I:"):
                self._field(line)
                if "instrument=" in line: in_body=True
                continue
            if in_body:
                self._music(line)

    def _field(self, line:str):
        k,_,v = line.partition(":"); v=v.strip(); h=self.header
        if k=="X":
            try: h.index=int(v)
            except: pass
        elif k=="T": h.title=v
        elif k=="C": h.composer=v
        elif k=="M": h.meter=v
        elif k=="Q":
            m=re.match(r'"([^"]+)"\s*([\d/=]+)?',v)
            if m:
                h.tempo_text=m.group(1)
                if m.group(2): h.tempo=m.group(2)
            else: h.tempo=v
        elif k=="K": self._key(v)
        elif k=="I" and "instrument=" in v:
            h.instrument=v.split("=",1)[1].strip().lower()

    def _key(self,v:str):
        h=self.header
        m=re.match(r'(\d)=([A-Gb#]+)\s*(m)?',v)
        if m:
            h.key_note=NOTE_NAMES.get(m.group(2), m.group(2).lower())
            h.key_mode="minor" if (m.group(3) or int(m.group(1))==6) else "major"

    def _voice_def(self, line:str):
        parts=line[2:].strip().split()
        if not parts: return
        name=parts[0]
        # 如果声部已存在则复用，否则新建
        if name in self.voices:
            vd = self.voices[name]
        else:
            vd = VoiceDef(name=name)
        for p in parts[1:]:
            if "=" in p:
                kk,vv=p.split("=",1)
                if kk=="stem": vd.stem=vv
                elif kk=="clef": vd.clef=vv
        self.voices[name]=vd
        self.current_voice=name
        if name not in self.voice_music:
            self.voice_music[name]=[]
            self.voice_order.append(name)

    def _ensure_voice(self):
        if self.current_voice is None:
            n="1"; self.current_voice=n
            self.voices[n]=VoiceDef(name=n)
            self.voice_music[n]=[]; self.voice_order.append(n)

    def _music(self, line:str):
        self._ensure_voice()
        h=self.header
        ks=lily_key_st(h.key_note)
        fl=use_flat(h.key_note)
        toks=tokenize_line(line, ks, h.key_mode, fl)
        self.voice_music[self.current_voice].extend(toks)


# ─────────────────────────────────────────────
# LilyPond 生成器
# ─────────────────────────────────────────────

class LilyPondGenerator:
    def __init__(self, p:NumNotationParser):
        self.p=p; self.h=p.header

    _DW = {"0":"Zero","1":"One","2":"Two","3":"Three","4":"Four",
            "5":"Five","6":"Six","7":"Seven","8":"Eight","9":"Nine"}

    def _varname(self, n: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9]", "", n)
        return "voice" + "".join(self._DW.get(c, c) for c in clean)

    def _tempo(self):
        m=re.match(r"(\d+)/(\d+)=(\d+)",self.h.tempo)
        if m:
            _,den,bpm=m.groups()
            return (f'\\tempo "{self.h.tempo_text}" {den}={bpm}'
                    if self.h.tempo_text else f"\\tempo {den}={bpm}")
        return f'\\tempo "{self.h.tempo_text}"' if self.h.tempo_text else "\\tempo 4=120"

    def _fmt(self, toks:list[NoteToken], guitar:bool=False) -> list[str]:
        h=self.h
        ks=lily_key_st(h.key_note); fl=use_flat(h.key_note)
        lines=[]; buf=[]
        for t in toks:
            s=token_to_lily(t, ks, h.key_mode, fl, guitar=guitar)
            if t.kind=="bar" and t.value=="|":
                buf.append("|"); lines.append(" ".join(buf)); buf=[]
            else:
                buf.append(s)
        if buf: lines.append(" ".join(buf))
        return lines

    def generate(self) -> str:
        out=['\\version "2.25.0"',""]
        out+=["global = {",
              f"  \\time {self.h.meter}",
              f"  {self._tempo()}",
              f"  \\key {self.h.key_note} \\{self.h.key_mode}",
              "}",""]
        is_guitar = self.h.instrument != "piano"
        for vname in self.p.voice_order:
            vd=self.p.voices.get(vname,VoiceDef(vname))
            var=self._varname(vname)
            out.append(f"{var} = \\absolute {{")
            out.append("  \\global")
            if vd.stem=="up": out.append("  \\voiceOne")
            elif vd.stem=="down": out.append("  \\voiceTwo")
            if vd.clef=="bass": out.append("  \\clef bass")
            for ml in self._fmt(self.p.voice_music[vname], guitar=is_guitar):
                out.append(f"  {ml}")
            out+=["}", ""]
        out+=["\\score {", self._staff(), "  \\layout { }", "  \\midi { }", "}"]
        return "\n".join(out)

    def _staff(self):
        return self._guitar() if self.h.instrument!="piano" else self._piano()

    def _guitar(self):
        instr = self.h.title or "Guitar"
        parts = [
            "  \\new StaffGroup <<",
            "    \\new Staff \\with {",
            f'      instrumentName = "{instr}"',
            "    } <<",
            '      \\clef "treble_8"',
        ]
        for v in self.p.voice_order:
            parts.append("      \\new Voice { \\" + self._varname(v) + " }")
        parts += [
            "    >>",
            "    \\new TabStaff \\with {",
            "      stringTunings = #guitar-tuning",
            "    } <<",
        ]
        for v in self.p.voice_order:
            parts.append("      \\new TabVoice { \\" + self._varname(v) + " }")
        parts += ["    >>", "  >>"]
        return "\n".join(parts)

    def _piano(self):
        treble = [v for v in self.p.voice_order
                  if self.p.voices.get(v, VoiceDef(v)).clef != "bass"]
        bass   = [v for v in self.p.voice_order
                  if self.p.voices.get(v, VoiceDef(v)).clef == "bass"]
        def voice_ref(v):
            return "      \\new Voice { \\" + self._varname(v) + " }"
        t_lines = "\n".join(voice_ref(v) for v in treble) if treble else "      { s1 }"
        b_lines = "\n".join(voice_ref(v) for v in bass)   if bass   else "      { s1 }"
        instr = self.h.title or "Piano"
        parts = []
        parts.append("  \\new PianoStaff \\with {")
        parts.append(f'    instrumentName = "{instr}"')
        parts.append("  } <<")
        parts.append("    \\new Staff <<")
        for v in treble:
            parts.append("      \\new Voice { \\" + self._varname(v) + " }")
        if not treble:
            parts.append("      { s1 }")
        parts.append("    >>")
        parts.append("    \\new Staff <<")
        for v in bass:
            parts.append("      \\new Voice { \\" + self._varname(v) + " }")
        if not bass:
            parts.append("      { s1 }")
        parts.append("    >>")
        parts.append("  >>")
        return "\n".join(parts)


# ─────────────────────────────────────────────
# 公共接口
# ─────────────────────────────────────────────

def convert(source:str) -> str:
    p=NumNotationParser(source); p.parse()
    return LilyPondGenerator(p).generate()


# ─────────────────────────────────────────────
# Demo 示例
# ─────────────────────────────────────────────

GUITAR_DEMO = """\
%numnotation-0.2
X:1
T:Etude in C
C:Carcassi
M:3/4
Q:1/4=96
K:1=C
I:instrument=guitar

V:1 stem=up
V:2 stem=down

V:1
!mf! (3_ 2_ 1_) 0. | 2 - - | (5_ 4_ 3_) 0. | 5 - - |

V:2
[1, 5, 3] 0 0 | [1, 5, 3] 0 0 | [4, 1 6,] 0 0 | [5, 2 5,] 0 0 |
"""

PIANO_DEMO = """\
%numnotation-0.2
X:1
T:Sonatina in G
M:4/4
Q:"Allegretto" 1/4=120
K:1=G
I:instrument=piano

V:1 clef=treble stem=up
V:2 clef=bass stem=down

V:1
!mf! 1_ 2_ 3_ 4_ 5_ 6_ 7_ 1'_ | 5 - 5 - | 6_ 5_ 4_ 3_ 2_ 1_ 7_ 6_ | 2 - - - |

V:2
[1,, 5,, 3,] - [1,, 5,, 3,] - | [1,, 5,, 3,] - [1,, 5,, 3,] - |
[4,, 1, 2,] - [4,, 1, 2,] - | [5,, 2, 7,,] - - - |
"""

# 更多时值的测试用例
DURATION_TEST = """\
%numnotation-0.2
X:2
T:Duration Test
M:4/4
Q:1/4=100
K:1=C
I:instrument=piano

V:1 clef=treble

V:1
1 - - - | 2 - 3 - | 4 5 6 7 | 1_ 2_ 3_ 4_ 5_ 6_ 7_ 1'_ |
1__ 2__ 3__ 4__ 5__ 6__ 7__ 1'__ 2'__ 3'__ 4'__ 5'__ 6'__ 7'__ 1''__ 2''__ | 1. 2_ 3 4 | 1_. 2__ 1_. 2__ 1_. 2__ 1_. 2__ | 1 - - - |
"""


def main():
    if len(sys.argv)<2:
        print("用法:")
        print("  python numnotation.py <input.num> [output.ly]")
        print("  python numnotation.py --demo-guitar")
        print("  python numnotation.py --demo-piano")
        print("  python numnotation.py --demo-duration")
        sys.exit(0)
    arg=sys.argv[1]
    if arg=="--demo-guitar":   print(convert(GUITAR_DEMO)); return
    if arg=="--demo-piano":    print(convert(PIANO_DEMO)); return
    if arg=="--demo-duration": print(convert(DURATION_TEST)); return
    with open(arg,encoding="utf-8") as f: source=f.read()
    result=convert(source)
    outfile=sys.argv[2] if len(sys.argv)>2 else re.sub(r'\.\w+$','',arg)+".ly"
    with open(outfile,"w",encoding="utf-8") as f: f.write(result)
    print(f"✓ {arg} → {outfile}")


if __name__=="__main__":
    main()
