import {stateFor} from './model.mjs';
export function mount(container,{kit:k,job}){
  const W=1100,H=440;
  const base={x:-185,y:-100,w:370,d:205,r:10};
  const P=k.fitProjection([...k.boxCorners(base,-22,10),[-170,-80,80],[170,90,80]],W,H,{pad:28,azimuth:48,elevation:38});
  const items=[];
  const put=(point,svg)=>items.push({depth:k.depthOf(point,P),svg});
  const slab=(plan,z,h,tone='mid',extra={})=>k.solidSvg(k.slabOf(plan,z,h,P,5,0.8),{tone,...extra});
  const cyl=(x,y,r,z,h,tone='mid',extra={})=>k.solidSvg(k.cylinder(x,y,r,z,h,P,32,.5),{tone,...extra});
  const line=(d,tone='lo')=>k.lineSvg(d,{tone});
  // Four feet, four columns and caps, eight real fasteners, one plate.
  for(const [x,y] of k.corners(base,18)){
    items.push({depth:-2000000,svg:cyl(x,y,8,-22,9,'lo')});
    put([x,y,4],cyl(x,y,3,0,5,'hi'));
  }
  items.push({depth:-1000000,svg:slab(base,-13,13,'mid')});
  for(const [x,y] of [[-162,-80],[162,-80],[-162,82],[162,82]]){
    put([x,y,26],cyl(x,y,4.5,0,52));
    put([x,y,53],cyl(x,y,7,52,3,'hi'));
    put([x,y,56],cyl(x,y,2,55,1,'lo'));
  }
  // The supported token rack, its four retaining rings and four token seats.
  items.push({depth:-500000,svg:slab({x:-150,y:-65,w:300,d:52,r:4},5,8)});
  for(const x of [-114,-38,38,114]){
    put([x,-40,17],cyl(x,-40,24,13,3,'lo'));
    put([x,-40,20],cyl(x,-40,20.5,16,2,'mid'));
    put([x,-40,26],`<g data-token="${(x+114)/76}">${cyl(x,-40,18,18,8,'hi',{lit:true})}${line(k.ring(x,-40,13.5,26,P),'mid')}</g>`);
  }
  // Six request carriers sit between two rails, with physical end stops.
  for(const y of [27,73]) items.push({depth:-400000,svg:slab({x:-155,y,w:310,d:4,r:1},1,8)});
  for(const x of [-158,154]) put([x,50,8],slab({x,y:23,w:6,d:58,r:2},0,12,'hi'));
  for(let i=0;i<6;i++){
    const x=-125+i*50;
    put([x,50,18],`<g data-request="${i}">${slab({x:x-19,y:34,w:38,d:32,r:3},9,10,'hi')}${line(k.lineOnTop([x-10,50],[x+10,50],19,P),'mid')}</g>`);
  }
  // Model-clock adjuster: base, knurled body, scale and screw. No free-floating lines.
  put([150,4,8],cyl(150,4,15,0,12));
  put([150,4,15],cyl(150,4,11,12,7,'hi')+line(k.knurl(150,4,11,12,19,32,P),'mid'));
  const ticks=k.radialTicks(150,4,9,20,5,19,[1,2],P);
  put([150,4,20],line(ticks.minor,'lo')+line(ticks.major,'mid'));
  const svg=k.figureSvg({width:W,height:H,label:'Token bucket teaching machine. Four token seats and six request carriers on supported rails.',body:items.sort((a,b)=>a.depth-b.depth).map(i=>i.svg)});
  container.innerHTML=svg;
  const tokens=[...container.querySelectorAll('[data-token]')],requests=[...container.querySelectorAll('[data-request]')];
  return {frame({scene,progress,setReadout}){
    const s=stateFor(scene.id,progress,job.model);
    tokens.forEach((node,i)=>{node.style.opacity=String(Math.max(.08,Math.min(1,s.tokens-i)));});
    requests.forEach((node,i)=>{
      const decision=s.decisions.find(d=>d.id===i);
      node.style.display=(scene.id==='transfer'&&i>=3)?'none':'';
      node.style.opacity=decision?(decision.pass?'1':'.20'):'0.7';
      const d=decision?.pass?8:0;
      node.setAttribute('transform',k.translateAlong(P,[0,d,0]));
    });
    setReadout(`MODEL t = ${s.clock.toFixed(1)} s  |  TOKENS ${s.tokens.toFixed(1)} / 4  |  PASS ${s.accepted}  |  DENY ${s.rejected}`);
  }};
}
