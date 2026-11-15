#!/usr/bin/env python3
"""Adversarial Gate A tests for comment/string/preprocessor liveness."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from liveness_20261115 import scan, LIVE, INACTIVE, UNKNOWN
from refint_20261115 import RefIndex

SRC = r'''
namespace Demo
{
    // public class CommentedType { }
    /* public class BlockCommented { } */
    public class Live
    {
        public DbSet<EmployeeChangeRequestDetail> EmployeeChangeRequestDetails { get; set; }
        public void LiveMethod()
        {
            var a = "http://x/*not-comment*/";
            var b = @"C:\tmp\file";
            var c = """raw // not comment""";
            var d = $"hello {name} // not comment";
            var e = "execute sp_TestProc";
            LiveCall();
            // CommentedCall();
            /* CommentedBlockCall(); */
            Context.Database.ExecuteSqlCommand("execute sp_TestProc");
        }
        // public void CommentedMethod() { CommentedMethodCall(); }
        public void Dead()
        {
            // LiveInsideComment();
        }
    }
#if false
    public class PreprocessorDead { public void DeadCall() { Dead(); } }
#else
    public class PreprocessorLive { public void LiveElse() { LiveCall(); } }
#endif
#if UNKNOWN_SYMBOL
    public class UnknownBranch { public void UnknownCall() { Unknown(); } }
#endif
}
'''

def test_liveness_states():
    r=scan(SRC)
    # the commented declaration/calls must not survive in code
    assert 'CommentedMethod' not in '\n'.join(r.lines)
    assert 'CommentedCall' not in '\n'.join(r.lines)
    assert 'BlockCommented' not in '\n'.join(r.lines)
    assert 'PreprocessorDead' not in '\n'.join(r.lines)
    assert 'PreprocessorLive' in '\n'.join(r.lines)
    assert 'UnknownBranch' not in '\n'.join(r.lines)
    # strings were blanked from code but retained
    assert any('sp_TestProc' in s['value'] for s in r.strings)
    assert any('not-comment' in s['value'] for s in r.strings)

def test_refindex_live_only():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); (root/'Demo.cs').write_text(SRC)
        refs=RefIndex(root)
        names={m['name'] for m in refs.methods}
        assert 'CommentedMethod' not in names
        assert 'DeadCall' not in names
        assert 'LiveMethod' in names
        m=next(x for x in refs.methods if x['name']=='LiveMethod')
        assert 'CommentedCall' not in m['calls']
        assert 'LiveCall' in m['calls']
        assert 'sp_TestProc' in m['procs']
        assert any(p['name']=='EmployeeChangeRequestDetails' and p['type']=='EmployeeChangeRequestDetail' for p in refs.props)

def test_gq_regression_commented():
    root=Path('/home/masis/.cache/zvec-grep/rag-requal-20261115/corpus')
    if not root.exists(): return
    refs=RefIndex(root)
    assert 'ChangeWorkProcess' not in {m['name'] for m in refs.methods}
    m=next(x for x in refs.methods if x['name']=='EditEmployeeJobAndGroup')
    assert 'ChangeWorkProcess' not in m['calls']
    assert 'sp_UpdateOrgTotalEmployee' not in m['procs']

if __name__=='__main__':
    test_liveness_states(); test_refindex_live_only(); test_gq_regression_commented()
    print('PASS liveness adversarial tests')
