// Reference dimensions only. Handling parameters belong to the uncalibrated demo.
export const SHIP_CATALOG_VERSION='reference-fleet-20260927';
const ship=(id,type,name,length,beam,source,sourceTitle,style,color,maxKn)=>({
 id,type,name,length,beam,source,sourceTitle,style,color,
 checked:'2026-09-27',handling:{maxKn,accelSeconds:Math.max(12,length*.55),turnSeconds:Math.max(5,length*.08)},
 handlingStatus:'illustrative_not_sea_trial_calibrated',
});
export const SHIPS=[
 ship('berlin','컨테이너선','Berlin Express',399,61,'https://www.hapag-lloyd.com/de/services-information/cargo-fleet/vessels/vessel/berlin-express.html','Hapag-Lloyd · 선대 제원','container','#328eab',22),
 ship('seat','초대형 원유운반선','SEATRIUMPH',333,60,'https://www.thenamaris.com/fleets/thenamaris-fleet/tankers/','Thenamaris · LOA/BEAM','tanker','#b9754e',15),
 ship('mayeda','LNG 운반선','Al Mayeda',345,54,'https://www.elengy.com/en/about-us/our-history','Elengy · Al Mayeda 입항 기록','lng','#659fbe',19),
 ship('aurora','자동차 운반선','Höegh Aurora',199.9,37.8,'https://kommunikasjon.ntb.no/pressemelding/18191346/verdens-storste-og-mest-miljovennlige-biltransportskip-dopt?lang=no&publisherId=17848888','Höegh Autoliners · 공식 보도자료','roro','#79a183',19),
 ship('stena','로로 여객선','Stena Hollandica',240.9,32,'https://stenalinefreight.com/app/uploads/2021/06/ff-2021-spreads-low-res.pdf','Stena Line · Freight Facts 2021','ferry','#377ec3',22),
 ship('queen','대양 여객선','Queen Mary 2',345.0336,39.9288,'https://www.cunard.com/content/dam/cunard/inventory-assets/ships/QM/9/qm2-deck-plans.pdf','Cunard · 1,132 ft / 131 ft를 m로 환산','liner','#a44d55',26),
 ship('okeanos','해양 탐사선','Okeanos Explorer',68,13,'https://oceanexplorer.noaa.gov/technology/noaa-ship-okeanos-explorer/','NOAA Ocean Exploration · 반올림된 미터 제원','research','#579897',10),
 ship('polarstern','쇄빙선','Polarstern',118,25,'https://www.awi.de/en/fleet-stations/research-vessel-and-cutter/research-vessel-and-icebreaker-polarstern.html','AWI · 선박 제원','icebreaker','#cb6f54',16),
 ship('spartacus','자항 준설선','Spartacus',164,34,'https://www.deme-group.com/technologies/spartacus','DEME · 기술 제원','dredger','#87a052',12),
 ship('asd','항만 예인선','Damen ASD Tug 2813',27.59,12.93,'https://www.damen.com/vessels/tugs/asd-tugs/asd-tug-2813','Damen · 실제 건조 시리즈 설계 제원','tug','#dfad55',12.7),
 ship('fcs','해상 작업자 이송선','Damen FCS 2710',26.8,10.5,'https://www.damen.com/vessels/offshore/crew-transfer-vessels/fast-crew-supplier-2710','Damen · 실제 건조 시리즈 설계 제원','catamaran','#629ac2',25),
 ship('patrol','연안 순찰선','Damen Stan Patrol 4207',42.8,7.1,'https://www.damen.com/vessels/defence-and-security/stan-patrol-vessels/stan-patrol-4207','Damen · 실제 건조 시리즈 설계 제원','patrol','#8d9ead',26.5),
];
export const shipById=id=>SHIPS.find(s=>s.id===id)||SHIPS[0];
