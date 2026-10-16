// Fetch Standard §2.9, checked 2026-10-16: https://fetch.spec.whatwg.org/#port-blocking
// OS-assigned ports can include these; an available TCP listener alone is insufficient.
export const FETCH_BLOCKED_PORTS=Object.freeze([
  0,1,7,9,11,13,15,17,19,20,21,22,23,25,37,42,43,53,69,77,79,87,95,
  101,102,103,104,109,110,111,113,115,117,119,123,135,137,139,143,161,179,
  389,427,465,512,513,514,515,526,530,531,532,540,548,554,556,563,587,
  601,636,989,990,993,995,1719,1720,1723,2049,3659,4045,4190,5060,5061,
  6000,6566,6665,6666,6667,6668,6669,6679,6697,10080,
]);
const blocked=new Set(FETCH_BLOCKED_PORTS);

export function isFetchCompatibleLoopback(url){
  if(!(url instanceof URL)||url.protocol!=="http:"||url.hostname!=="127.0.0.1"||url.username||url.password)return false;
  const port=Number(url.port||80);
  return Number.isInteger(port)&&port>0&&port<=65535&&!blocked.has(port);
}

// Only endpoint allocation is repeated. No binding lookup or task SQL runs here.
export async function createLoopbackRuntime(Miniflare, options, candidates=[0]){
  const rejected=[];
  for(let attempt=0;attempt<8;attempt++){
    const emulator=new Miniflare({...options,port:candidates[attempt]??0});
    let url;
    try{url=await emulator.ready;}
    catch(error){await emulator.dispose();throw error;}
    if(isFetchCompatibleLoopback(url))return {emulator,allocation:{attempts:attempt+1,port:Number(url.port||80),rejected_ports:rejected}};
    const port=Number(url.port||80);
    await emulator.dispose();
    if(url.protocol!=="http:"||url.hostname!=="127.0.0.1"||url.username||url.password)throw new Error("unexpected local SDK origin");
    rejected.push(port);
  }
  const error=new Error("no fetch-compatible local SDK endpoint allocated before D1 access");
  error.code="LOCAL_FETCH_PORT_EXHAUSTED";
  throw error;
}
