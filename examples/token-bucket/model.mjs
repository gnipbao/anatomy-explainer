// Request-count teaching model. Network packet bytes are intentionally simplified.
export function simulate(config, events, time){
  let tokens=config.initial_tokens,previous=0,accepted=0,rejected=0;
  const decisions=[];
  for(const event of events){
    if(event.time>time) break;
    if(event.time<previous) throw Error('events must be sorted');
    tokens=Math.min(config.capacity,tokens+config.refill_per_second*(event.time-previous));
    const pass=tokens>=config.request_cost;
    if(pass){tokens-=config.request_cost;accepted++;}else rejected++;
    decisions.push({id:event.id,pass,time:event.time});previous=event.time;
  }
  tokens=Math.min(config.capacity,tokens+config.refill_per_second*Math.max(0,time-previous));
  return {tokens,accepted,rejected,decisions};
}
export function stateFor(sceneId, progress, model){
  if(sceneId==='predict') return {...simulate(model,[],0),clock:0,pending:6};
  const transfer=sceneId==='transfer';
  const count=transfer?3:6,at=transfer?2:0.5;
  const clock=transfer?Math.min(2,progress*3):Math.min(4.5,progress*5);
  const config={...model,initial_tokens:transfer?0:model.initial_tokens};
  const events=Array.from({length:count},(_,id)=>({id,time:at}));
  return {...simulate(config,events,clock),clock,pending:clock<at?count:0};
}
