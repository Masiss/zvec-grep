#!/usr/bin/env python3
"""Lexical C# liveness/comment/string scanner for the experimental index.

This is not a C# compiler. It is a conservative scanner for:
- line and block comments;
- regular, verbatim, raw and interpolated string bodies;
- char literals;
- simple #if false/#if 0 and #else/#endif branches; unknown symbols are UNKNOWN.

Code lines have comments/strings/inactive code blanked (newlines preserved).
String values are retained separately for SQL invocation discovery.
"""
from __future__ import annotations
from dataclasses import dataclass, field

LIVE='LIVE'; INACTIVE='INACTIVE'; UNKNOWN='UNKNOWN'

@dataclass
class ScanResult:
    lines: list[str]
    strings: list[dict] = field(default_factory=list)
    line_liveness: list[str] = field(default_factory=list)
    directives: list[str] = field(default_factory=list)
    unterminated: bool = False

def _blank(chars, a, b):
    for k in range(a,b):
        if chars[k] not in '\r\n': chars[k]=' '

def _scan(text: str):
    chars=list(text); n=len(chars); strings=[]; i=0; line=1; unterminated=False
    def line_at(pos): return text.count('\n',0,pos)+1
    while i<n:
        c=chars[i]
        if c=='/' and i+1<n and chars[i+1]=='/':
            a=i
            while i<n and chars[i] not in '\r\n': i+=1
            _blank(chars,a,i); continue
        if c=='/' and i+1<n and chars[i+1]=='*':
            a=i; i+=2
            while i+1<n and not (chars[i]=='*' and chars[i+1]=='/'): i+=1
            if i+1<n: i+=2
            else: unterminated=True
            _blank(chars,a,i); continue
        # string prefixes and quotes
        start=i; raw=False; verbatim=False; interpolated=False
        if c=='$' and i+1<n and chars[i+1]=='"': interpolated=True; i+=1; c=chars[i]
        elif c=='@' and i+1<n and chars[i+1]=='"': verbatim=True; i+=1; c=chars[i]
        elif c=='$' and i+2<n and chars[i+1]=='@' and chars[i+2]=='"': interpolated=True; verbatim=True; i+=2; c=chars[i]
        elif c=='@' and i+2<n and chars[i+1]=='$' and chars[i+2]=='"': interpolated=True; verbatim=True; i+=2; c=chars[i]
        if c in ('"', "'"):
            quote=c
            raw = quote=='"' and i+2<n and chars[i+1]=='"' and chars[i+2]=='"'
            j=i
            if raw:
                j=i+3
                while j+2<n and not (chars[j]=='"' and chars[j+1]=='"' and chars[j+2]=='"'): j+=1
                end=j+3 if j+2<n else n
                value=''.join(chars[i+3:j]); unterm=j+2>=n
            elif verbatim:
                j=i+1
                while j<n:
                    if chars[j]=='"':
                        if j+1<n and chars[j+1]=='"': j+=2; continue
                        j+=1; break
                    j+=1
                end=j; value=''.join(chars[i+1:j-1] if j>i+1 else ''); unterm=j>=n
            else:
                j=i+1
                while j<n:
                    if chars[j]=='\\': j+=2; continue
                    if chars[j]==quote: j+=1; break
                    j+=1
                end=j; value=''.join(chars[i+1:j-1] if j>i+1 else ''); unterm=j>=n
            if not unterm:
                strings.append({'value':value,'start_line':line_at(start),'end_line':line_at(end-1),'verbatim':verbatim,'raw':raw,'interpolated':interpolated})
            _blank(chars,start,end); i=end; unterminated=unterminated or unterm; continue
        i+=1
    return ''.join(chars), strings, unterminated

def _preprocess(code: str) -> tuple[list[str],list[str],list[str]]:
    lines=code.split('\n'); liveness=[LIVE]*len(lines); directives=[]
    stack=[]  # (parent_status, branch_status, seen_else)
    def current(): return stack[-1][1] if stack else LIVE
    for idx,line in enumerate(lines):
        stripped=line.lstrip()
        if not stripped.startswith('#'): liveness[idx]=current(); continue
        directives.append(stripped)
        parts=stripped[1:].strip().split(None,1); kw=parts[0] if parts else ''; expr=parts[1].strip() if len(parts)>1 else ''
        parent=current()
        if kw=='if':
            cond=LIVE if parent!=LIVE else (INACTIVE if expr in ('false','0') else (LIVE if expr in ('true','1') else UNKNOWN))
            stack.append((parent,cond,False)); liveness[idx]=UNKNOWN
        elif kw in ('else','elif'):
            if stack:
                p,b,seen=stack.pop(); ns=INACTIVE if b==LIVE else (LIVE if b==INACTIVE else UNKNOWN); stack.append((p,ns,True)); liveness[idx]=UNKNOWN
            else: liveness[idx]=UNKNOWN
        elif kw=='endif':
            if stack: stack.pop()
            liveness[idx]=LIVE if not stack else UNKNOWN
        else:
            liveness[idx]=UNKNOWN
        # directive line is not code
        lines[idx]=''
    # anything left open is not reliable
    if stack:
        # mark remaining unknown lines conservatively
        for idx in range(len(lines)): liveness[idx]=UNKNOWN
    # blank inactive/unknown code lines, retaining newline
    for idx,line in enumerate(lines):
        if liveness[idx]!=LIVE: lines[idx]=''
    return lines, liveness, directives

def scan(text: str) -> ScanResult:
    code, strings, unterm=_scan(text)
    lines, liveness, directives=_preprocess(code)
    return ScanResult(lines=lines, strings=strings, line_liveness=liveness, directives=directives, unterminated=unterm)

def code_text(text: str) -> str:
    return '\n'.join(scan(text).lines)
