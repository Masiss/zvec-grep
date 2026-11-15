use super::*;

#[test]
fn line_offsets_excludes_the_trailing_terminator() {
    let text = "alpha\nbeta\ngamma\n";
    assert_eq!(line_offsets(text, 1, 1).expect("valid line bounds"), (0, 5));
    assert_eq!(line_offsets(text, 2, 2).expect("valid line bounds"), (6, 10));
    assert_eq!(line_offsets(text, 1, 3).expect("valid line bounds"), (0, 16));
}

#[test]
fn line_offsets_handles_crlf_and_missing_final_newline() {
    let text = "alpha\r\nbeta\r\ngamma";
    assert_eq!(line_offsets(text, 1, 1).expect("valid line bounds"), (0, 5));
    assert_eq!(line_offsets(text, 3, 3).expect("valid line bounds"), (13, 18));
    assert_eq!(line_offsets(text, 1, 3).expect("valid line bounds"), (0, 18));
}

#[test]
fn line_offsets_rejects_zero_reversed_and_past_eof() {
    let text = "alpha\nbeta\n";
    assert_eq!(line_offsets(text, 0, 1), Err(EvidenceGapCode::InvalidRange));
    assert_eq!(line_offsets(text, 2, 1), Err(EvidenceGapCode::InvalidRange));
    assert_eq!(line_offsets(text, 1, 3), Err(EvidenceGapCode::InvalidRange));
    assert_eq!(line_offsets("", 1, 1), Err(EvidenceGapCode::InvalidRange));
}

#[test]
fn empty_line_is_an_empty_range_not_a_gap() {
    let text = "alpha\n\nbeta\n";
    assert_eq!(line_offsets(text, 2, 2).expect("valid line bounds"), (6, 6));
}
