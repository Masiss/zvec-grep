def normalize_invoice_number(value):
    return value.strip().upper()


def invoice_note(value):
    return value


def invoice_notification(value):
    return value


def create_invoice(value):
    return normalize_invoice_number(value) + invoice_note(value)


def send_invoice_notification(value):
    return invoice_notification(value)
