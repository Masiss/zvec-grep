pub struct AccountSnapshot {
    pub account_id: String,
}

pub fn rebuild_account_cache(account_id: &str) -> AccountSnapshot {
    AccountSnapshot {
        account_id: account_id.to_owned(),
    }
}

pub fn accept_account(account_id: &str) {
    let _snapshot = rebuild_account_cache(account_id);
    publish_account_event(account_id);
}

pub fn publish_account_event(account_id: &str) {
    let _ = account_id;
}

pub fn write_account_audit(account_id: &str) {
    let _ = account_id;
}
