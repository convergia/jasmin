// Private qualification fixture, JSON-lines RPC over stdin/stdout, no HTTP API.
const smpp = require('smpp');
const readline = require('readline');
const http = require('http');
const PDU = require('smpp/lib/pdu').PDU;
const original = PDU.prototype.fromBuffer;
PDU.prototype.fromBuffer = function (buffer) {
  const value = original.call(this, buffer);
  this.wire = Buffer.from(buffer);
  return value;
};
let server, client, mode = 'accept', counter = 0, rejectLeft = 0;
const upstream = new Set(), events = [], pending = [];
function record(event) { events.push({index: events.length, ...event}); }
function snapshot(pdu) {
  // Read short_message directly from the received wire, avoiding codec round trips.
  const b = pdu.wire;
  let i = 16;
  const cstring = () => { while (b[i++] !== 0) { if (i > b.length) throw Error('Bad PDU'); } };
  cstring(); i += 2; cstring(); i += 2; cstring(); i += 3;
  cstring(); cstring(); i += 4;
  const size = b[i++];
  return {hex: b.subarray(i, i + size).toString('hex'), coding: pdu.data_coding,
    esm: pdu.esm_class, source: pdu.source_addr, destination: pdu.destination_addr,
    sar_ref: pdu.sar_msg_ref_num, sar_total: pdu.sar_total_segments, sar_seq: pdu.sar_segment_seqnum};
}
function request(session, name, options, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error(name + ' response timeout')), timeout);
    session[name](options, pdu => { clearTimeout(timer); resolve(pdu); });
  });
}
function options(input) {
  const result = {source_addr: input.source || '15559990001', source_addr_ton: 1, source_addr_npi: 1,
    destination_addr: input.destination || '15551230100', dest_addr_ton: 1, dest_addr_npi: 1,
    data_coding: input.coding || 0, esm_class: input.esm || 0,
    registered_delivery: input.dlr ? 1 : 0, short_message: Buffer.from(input.hex || '', 'hex')};
  if (input.sar_ref !== undefined) Object.assign(result, {sar_msg_ref_num: input.sar_ref,
    sar_total_segments: input.sar_total, sar_segment_seqnum: input.sar_seq});
  return result;
}
async function rpc(input) {
  switch (input.op) {
    case 'start':
      server = smpp.createServer(session => {
        session.on('error', () => record({type: 'upstream_error'}));
        session.on('close', () => { upstream.delete(session); record({type: 'upstream_closed'}); });
        const bind = pdu => {
          if (pdu.system_id !== process.env.SMPP_SYSTEM_ID || pdu.password !== process.env.SMPP_PASSWORD) {
            session.send(pdu.response({command_status: smpp.ESME_RBINDFAIL})); return;
          }
          upstream.add(session); session.send(pdu.response()); record({type: 'upstream_bind'});
        };
        session.on('bind_transceiver', bind);
        session.on('enquire_link', pdu => session.send(pdu.response()));
        session.on('unbind', pdu => { session.send(pdu.response()); session.close(); });
        session.on('submit_sm', pdu => {
          const id = String(++counter).padStart(8, '0');
          const observation = {type: 'mt', ...snapshot(pdu), id, outcome: 'accepted'};
          if (mode === 'drop') observation.outcome = 'unanswered';
          if (rejectLeft > 0) { observation.outcome = 'rejected'; rejectLeft--; }
          record(observation);
          if (observation.outcome === 'unanswered') return;
          if (observation.outcome === 'rejected') {
            session.send(pdu.response({command_status: smpp.ESME_RTHROTTLED})); return;
          }
          session.send(pdu.response({message_id: id}));
          if (pdu.registered_delivery & 1) pending.push({id, source: pdu.destination_addr,
            destination: pdu.source_addr});
        });
      });
      await new Promise(resolve => server.listen(2776, '127.0.0.1', resolve));
      const webhook = http.createServer((req, res) => {
        let body = '';
        req.on('data', chunk => { body += chunk; });
        req.on('end', () => {
          const form = new URLSearchParams(body);
          // Jasmin 0.11.1 sends coding as a single encoded octet in its HTTP callback.
          const dcs = form.get('coding');
          const coding = dcs && dcs.length === 1 && dcs.charCodeAt(0) < 10 ? dcs.charCodeAt(0) : Number(dcs);
          record({type: 'http_mo', hex: form.get('binary'), coding,
            source: form.get('from'), destination: form.get('to'), id: form.get('id')});
          res.end('ACK/Jasmin');
        });
      });
      await new Promise(resolve => webhook.listen(2777, '127.0.0.1', resolve));
      return {ready: true};
    case 'connect':
      client = smpp.connect({host: '127.0.0.1', port: 2775, auto_enquire_link_period: 1000});
      client.on('error', () => record({type: 'customer_error'}));
      client.on('close', () => record({type: 'customer_closed'}));
      client.on('deliver_sm', pdu => {
        record({type: (pdu.esm_class & 4) ? 'dlr' : 'mo', ...snapshot(pdu),
          receipt_id: String(pdu.receipted_message_id || '')});
        client.send(pdu.response());
      });
      const bound = await request(client, 'bind_transceiver', {
        system_id: process.env.SMPP_SYSTEM_ID, password: process.env.SMPP_PASSWORD});
      if (bound.command_status) throw Error('Customer bind rejected ' + bound.command_status);
      return {bound: true};
    case 'submit':
      const response = await request(client, 'submit_sm', options(input));
      return {status: response.command_status, id: response.message_id};
    case 'inject':
      if (upstream.size !== 1) throw Error('Exactly one private upstream bind required');
      const mo = await request([...upstream][0], 'deliver_sm', options(input));
      return {status: mo.command_status};
    case 'receipts':
      if (upstream.size !== 1) throw Error('No upstream receiving bind');
      for (const receipt of pending.splice(0)) {
        const result = await request([...upstream][0], 'deliver_sm', {
          source_addr: receipt.source, destination_addr: receipt.destination, esm_class: 4,
          receipted_message_id: receipt.id, message_state: 2,
          short_message: 'id:' + receipt.id + ' sub:001 dlvrd:001 submit date:2610091200 done date:2610091201 stat:DELIVRD err:000 text:'});
        record({type: 'dlr_sent', id: receipt.id, status: result.command_status});
      }
      return {sent: true};
    case 'mode': mode = input.mode; rejectLeft = input.reject || 0; return {mode};
    case 'disconnect': for (const session of upstream) session.close(); return {disconnected: true};
    case 'events': return {events, upstream_binds: upstream.size};
    default: throw Error('Unknown private fixture operation');
  }
}
const lines = readline.createInterface({input: process.stdin});
let chain = Promise.resolve();
lines.on('line', line => {
  chain = chain.then(async () => {
    try { console.log(JSON.stringify({ok: true, result: await rpc(JSON.parse(line))})); }
    catch (error) { console.log(JSON.stringify({ok: false, error: error.message})); }
  });
});
lines.on('close', () => process.exit(0));
