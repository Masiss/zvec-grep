export function parse_widget_payload(value: string): string {
  return value.trim();
}

export function get_widget_payment(value: string): string {
  return value;
}

export function submit_widget(value: string): string {
  return parse_widget_payload(value) + get_widget_payment(value);
}

export function load_gadget(value: string): string {
  return value;
}
