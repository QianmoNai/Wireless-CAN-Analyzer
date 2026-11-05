// Queue producers can publish after tick captured its timestamp. The real
// dequeue path must use current time, including around uint32_t tick wrap.
void dual_a_test_fresh_queue_time(void) {
  assert(bound && session.online && !pending_size);
  dual_session_t saved=session;
  uint32_t can_drops=atomic_load(&drop_count),uart_drops=atomic_load(&uart_drop_count);
  uint32_t epoch=atomic_load(&generation);
  for (unsigned skew=1;skew<=10;skew+=9) {
    can_message_t frame={.created=now_ms(),.generation=epoch,.data={0,8}};
    dual_put32(frame.data+2,0x643);
    assert(xQueueSend(can_out,&frame,0)==pdTRUE);
    prepare_can(now_ms()-skew);
    assert(pending_count==1 && pending_size && atomic_load(&drop_count)==can_drops);
    pending_count=pending_size=0;
    live_uart=true;atomic_store(&requested_uart,true);
    uart_message_t bytes={.created=now_ms(),.generation=epoch,.size=2,.data={0x31,0x32}};
    assert(xQueueSend(uart_out,&bytes,0)==pdTRUE);
    prepare_uart(now_ms()-skew);
    assert(pending_count==2 && pending_size && atomic_load(&uart_drop_count)==uart_drops);
    pending_count=pending_size=0;
    live_uart=false;atomic_store(&requested_uart,false);
  }
  // The expiry boundary still drops genuinely stale CAN data.
  uint32_t expired_before=atomic_load(&diagnostics[CAN_QUEUE_EXPIRED]);
  can_message_t expired={.created=now_ms()-DUAL_FRAME_TTL_MS,.generation=epoch,.data={0,0}};
  assert(xQueueSend(can_out,&expired,0)==pdTRUE);
  prepare_can(now_ms());
  assert(!pending_size && atomic_load(&drop_count)==can_drops+1);
  assert(atomic_load(&diagnostics[CAN_QUEUE_EXPIRED])==expired_before+1);
  uint8_t request[20]={1,DUAL_USB_COMMAND,7},reply[20];
  dual_put32(request+16,123);
  assert(!dual_link_usb_command(request,reply));
  assert(dual_u32(reply+4)==expired_before+1 && dual_u32(reply+16)==123);
  uint32_t failed_before=atomic_load(&diagnostics[RADIO_SEND_FAILED]);
  sent(NULL,ESP_NOW_SEND_FAIL);
  request[2]=8;assert(!dual_link_usb_command(request,reply));
  assert(dual_u32(reply+4)==failed_before+1);
  request[3]=DUAL_A;assert(dual_link_usb_command(request,reply)==0x13);
  request[3]=0;request[2]=10;assert(dual_link_usb_command(request,reply)==0x13);
  atomic_store(&drop_count,can_drops);pending_type=0;session=saved;
}
