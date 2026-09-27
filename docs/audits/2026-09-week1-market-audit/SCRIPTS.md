# Scripts de la Auditoría Semana 1

Copia literal de los scripts ejecutados el 2026-09-27. Se guardan como Markdown, y no como `.py`, para que no entren en `ruff check .` del proyecto (son código de análisis puntual, no de producción).

Para reejecutarlos: copiar cada bloque a un fichero `.py` fuera del repositorio y lanzarlo con `.venv/bin/python`. Todos reciben como argumento un directorio de trabajo para sus CSV intermedios; `q.py` lee el SQL por stdin. Todas las consultas son de solo lectura.

## `q.py`

```
import sys, psycopg
from psycopg.rows import dict_row
from edgecourt.config import get_settings
from edgecourt.db.connection import dsn_from_env
dsn = dsn_from_env(get_settings())
sql = sys.stdin.read()
with psycopg.connect(dsn, row_factory=dict_row) as c:
    c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    c.execute("SET default_transaction_read_only = on")
    for stmt in [s for s in sql.split(";\n") if s.strip()]:
        cur = c.execute(stmt)
        if cur.description:
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
            print(" | ".join(cols))
            for r in rows: print(" | ".join(str(r[k]) for k in cols))
            print("---")
    c.rollback()
```

## `audit.py`

```
import psycopg, pandas as pd, numpy as np, sys
from psycopg.rows import dict_row
from edgecourt.config import get_settings
from edgecourt.db.connection import dsn_from_env
pd.set_option("display.width",250); pd.set_option("display.max_columns",40); pd.set_option("display.max_rows",500)
st=get_settings(); print("MINIMUM_LIQUIDITY efectivo:", st.minimum_liquidity)
S=sys.argv[1]
with psycopg.connect(dsn_from_env(st), row_factory=dict_row) as c:
    c.execute("SET default_transaction_read_only = on")
    obs=pd.DataFrame(c.execute("""select o.*, m.market_start_time, m.final_status, e.competition_name, e.event_name, e.country_code
        from market_observation o join betfair_market m using(market_id) join betfair_event e using(event_id)""").fetchall())
    rp=pd.DataFrame(c.execute("select * from runner_price").fetchall())
    mk=pd.DataFrame(c.execute("select m.*, e.competition_name, e.event_name from betfair_market m join betfair_event e using(event_id)").fetchall())
    c.rollback()
for col in ["minutes_to_start","total_matched","total_available","best_back_available","best_lay_available","max_spread_pct"]:
    obs[col]=obs[col].astype(float)
for col in rp.columns:
    if col.startswith(("back_","lay_","last_price","runner_total")): rp[col]=rp[col].astype(float)
obs.to_csv(f"{S}/observations.csv",index=False); rp.to_csv(f"{S}/runner_price.csv",index=False)

# --- Tick ladder de Betfair
LAD=[(1.01,2,0.01),(2,3,0.02),(3,4,0.05),(4,6,0.1),(6,10,0.2),(10,20,0.5),(20,30,1),(30,50,2),(50,100,5),(100,1000,10)]
ladder=[]
for lo,hi,step in LAD:
    x=lo
    while x<hi-1e-9: ladder.append(round(x,2)); x+=step
ladder.append(1000.0); ladder=np.array(ladder)
def tick(p): return int(np.argmin(np.abs(ladder-p)))
rp2=rp.dropna(subset=["back_price_1","lay_price_1"]).copy()
rp2["ticks"]=[tick(l)-tick(b) for b,l in zip(rp2.back_price_1,rp2.lay_price_1)]
rp2["spread_pct"]=(rp2.lay_price_1-rp2.back_price_1)/rp2.back_price_1*100
rp2["top_depth"]=rp2[["back_size_1","lay_size_1"]].min(axis=1)
agg=rp2.groupby("observation_id").agg(max_ticks=("ticks","max"),min_ticks=("ticks","min"),top_depth_min=("top_depth","min"),n2=("ticks","size"))
# favorito = menor back_price_1
fav=rp2.sort_values("back_price_1").groupby("observation_id").first()[["back_price_1","lay_price_1","spread_pct","ticks"]].rename(columns=lambda c:"fav_"+c)
bk=rp.groupby("observation_id").apply(lambda g: pd.Series({
    "book_back": (1/g.back_price_1).sum() if g.back_price_1.notna().all() and len(g)==2 else np.nan,
    "book_lay": (1/g.lay_price_1).sum() if g.lay_price_1.notna().all() and len(g)==2 else np.nan,
    "n_runner_rows": len(g), "one_sided": int(((g.back_price_1.isna())^(g.lay_price_1.isna())).any())}), include_groups=False)
obs=obs.merge(agg,left_on="observation_id",right_index=True,how="left").merge(fav,left_on="observation_id",right_index=True,how="left").merge(bk,left_on="observation_id",right_index=True,how="left")
obs["has_spread"]=obs.max_spread_pct.notna()
obs["liq_ge_min"]=obs.total_available>=st.minimum_liquidity

def band(m):
    if m<=10: return "A0-10"
    if m<=30: return "A10-30"
    if m<=90: return "A30-90"
    return "A90-360"
obs["group"]=obs.snapshot_label
obs.loc[obs.snapshot_label=="adaptive","band"]=obs.loc[obs.snapshot_label=="adaptive","minutes_to_start"].map(band)
ORDER=["24h","12h","6h","1h","10m","close","adaptive","A90-360","A30-90","A10-30","A0-10","ALL"]
def subsets():
    for g in ["24h","12h","6h","1h","10m","close","adaptive"]: yield g, obs[obs.snapshot_label==g]
    for b in ["A90-360","A30-90","A10-30","A0-10"]: yield b, obs[obs.band==b]
    yield "hitos(todos)", obs[obs.snapshot_label!="adaptive"]
    yield "ALL", obs
def q(s,p): s=s.dropna(); return round(float(np.percentile(s,p)),2) if len(s) else np.nan
def pct(x): return round(100*float(np.mean(x)),1) if len(x) else np.nan

print("\n### T1 — Por OBSERVACIÓN: cobertura, liquidez (total_available, €)")
R=[]
for n,d in subsets():
    L=d.total_available[d.total_available>0]
    R.append(dict(grupo=n,mercados=d.market_id.nunique(),obs=len(d),pct_precios=pct(d.has_prices),pct_liq=pct(d.has_liquidity),
        pct_spread2l=pct(d.has_spread),pct_liq_ge_min=pct(d.liq_ge_min),liq_p50_incl0=q(d.total_available,50),
        liq_p25=q(L,25),liq_p50=q(L,50),liq_p75=q(L,75),liq_p90=q(L,90)))
T1=pd.DataFrame(R); print(T1.to_string(index=False))

print("\n### T2 — Por OBSERVACIÓN: spread BACK/LAY (max_spread_pct, peor runner) sobre obs con ambos lados")
R=[]
for n,d in subsets():
    s=d.max_spread_pct.dropna()
    R.append(dict(grupo=n,n_spread=len(s),p25=q(s,25),p50=q(s,50),p75=q(s,75),p90=q(s,90),
        le1=pct(s<=1),le2=pct(s<=2),le5=pct(s<=5),le10=pct(s<=10),gt10=pct(s>10),
        ticks_p50=q(d.max_ticks,50),ticks_p90=q(d.max_ticks,90),fav_spread_p50=q(d.fav_spread_pct,50),
        book_back_p50=q(d.book_back,50),book_lay_p50=q(d.book_lay,50),top_depth_p50=q(d.top_depth_min,50)))
T2=pd.DataFrame(R); print(T2.to_string(index=False))

print("\n### T3 — Por OBSERVACIÓN: total_matched (€)")
R=[]
for n,d in subsets():
    t=d.total_matched
    R.append(dict(grupo=n,n_notnull=int(t.notna().sum()),pct_gt0=pct(t.fillna(0)>0),p25=q(t,25),p50=q(t,50),p75=q(t,75),p90=q(t,90),max=q(t,100),
        p50_si_gt0=q(t[t>0],50)))
T3=pd.DataFrame(R); print(T3.to_string(index=False))

# --- Por MERCADO: primero se resume cada mercado dentro del grupo, luego distribución entre mercados
print("\n### T4 — Por MERCADO (cada mercado pesa 1): se agrega por mercado dentro del grupo y luego se describe entre mercados")
R=[]
for n,d in subsets():
    pm=d.groupby("market_id").agg(any_p=("has_prices","max"),any_l=("has_liquidity","max"),frac_p=("has_prices","mean"),
        liq_med=("total_available","median"),sp_med=("max_spread_pct","median"),tm_max=("total_matched","max"),
        liqmin=("liq_ge_min","max"))
    liq=pm.liq_med[pm.liq_med>0]; sp=pm.sp_med.dropna()
    R.append(dict(grupo=n,mercados=len(pm),pct_mk_alguna_vez_precios=pct(pm.any_p),pct_mk_liq=pct(pm.any_l),pct_mk_liq_ge_min=pct(pm.liqmin),
        frac_obs_con_precio_mediana=round(pm.frac_p.median()*100,1),
        liq_med_p25=q(liq,25),liq_med_p50=q(liq,50),liq_med_p75=q(liq,75),
        n_mk_spread=len(sp),sp_p25=q(sp,25),sp_p50=q(sp,50),sp_p75=q(sp,75),sp_p90=q(sp,90),
        mk_sp_le1=pct(sp<=1),mk_sp_le2=pct(sp<=2),mk_sp_le5=pct(sp<=5),mk_sp_le10=pct(sp<=10),mk_sp_gt10=pct(sp>10),
        tm_max_p50=q(pm.tm_max,50),tm_max_p90=q(pm.tm_max,90)))
T4=pd.DataFrame(R); print(T4.to_string(index=False))

# Vista global por mercado (todas sus observaciones)
pm=obs.groupby("market_id").agg(comp=("competition_name","first"),event=("event_name","first"),start=("market_start_time","first"),
    n_obs=("observation_id","size"),n_adapt=("snapshot_label",lambda s:(s=="adaptive").sum()),
    labels=("snapshot_label",lambda s:",".join(sorted(set(s)-{"adaptive"}))),
    any_p=("has_prices","max"),frac_p=("has_prices","mean"),liq_max=("total_available","max"),liq_med=("total_available","median"),
    sp_min=("max_spread_pct","min"),sp_med=("max_spread_pct","median"),tm_max=("total_matched","max"),first_mts_price=("minutes_to_start",lambda s: np.nan))
fp=obs[obs.has_prices].groupby("market_id").minutes_to_start.max(); pm["max_mts_con_precio"]=fp
pm["adaptive"]=pm.n_adapt>0
print("\n### T5 — Mercados: adaptive vs no-adaptive")
print(pm.groupby("adaptive").agg(mercados=("n_obs","size"),obs=("n_obs","sum"),alguna_vez_precios=("any_p","sum"),
    obs_por_mk_p50=("n_obs","median"),liq_max_p50=("liq_max","median"),sp_med_p50=("sp_med","median"),tm_max_p50=("tm_max","median")).to_string())
print("\nPeso de los 26 mercados adaptive:", pm[pm.adaptive].n_obs.sum(), "de", len(obs), "obs =", round(100*pm[pm.adaptive].n_obs.sum()/len(obs),1),"%")
print("\n### T6 — Por competición (por mercado)")
print(pm.groupby("comp").agg(mercados=("n_obs","size"),obs=("n_obs","sum"),mk_con_precios=("any_p","sum"),mk_adaptive=("adaptive","sum"),
    liq_max_p50=("liq_max","median"),sp_med_p50=("sp_med","median"),tm_max_p50=("tm_max","median")).sort_values("mercados",ascending=False).to_string())
print("\n### T7 — Listado completo por mercado")
print(pm.drop(columns=["first_mts_price"]).sort_values("start").round(2).to_string())
pm.to_csv(f"{S}/per_market.csv")

# --- Evolución intramercado
print("\n### T8 — Evolución intramercado (26 mercados adaptive): mediana por mercado en cada banda de minutos al inicio")
bands=[(360,1500,"hito >360"),(90,360,"90-360"),(30,90,"30-90"),(10,30,"10-30"),(3,10,"3-10"),(0,3,"0-3")]
ad_ids=pm[pm.adaptive].index
a=obs[obs.market_id.isin(ad_ids)].copy()
a["b"]=pd.cut(a.minutes_to_start,[b[0] for b in bands[::-1]]+[1500],labels=[b[2] for b in bands[::-1]],include_lowest=True)
ev=a.groupby(["market_id","b"],observed=True).agg(n=("observation_id","size"),frac_p=("has_prices","mean"),liq=("total_available","median"),
    sp=("max_spread_pct","median"),tk=("max_ticks","median"),tm=("total_matched","median")).reset_index()
E=ev.groupby("b",observed=True).agg(mercados=("market_id","nunique"),obs=("n","sum"),frac_precio_mediana=("frac_p","median"),
    liq_p25=("liq",lambda s:q(s,25)),liq_p50=("liq","median"),liq_p75=("liq",lambda s:q(s,75)),
    sp_p25=("sp",lambda s:q(s,25)),sp_p50=("sp","median"),sp_p75=("sp",lambda s:q(s,75)),ticks_p50=("tk","median"),
    tm_p50=("tm","median"),tm_p75=("tm",lambda s:q(s,75)))
print(E.round(2).to_string())
# Pareado: mismo mercado, primera vs última obs con spread
print("\n### T9 — Pareado por mercado adaptive: primera vs última observación con precio")
R=[]
for mid,g in a[a.has_prices].sort_values("observed_at").groupby("market_id"):
    f,l=g.iloc[0],g.iloc[-1]
    R.append(dict(market_id=mid,event=f.event_name[:38],n_con_precio=len(g),mts_first=round(f.minutes_to_start,1),mts_last=round(l.minutes_to_start,1),
        liq_first=round(f.total_available),liq_last=round(l.total_available),sp_first=f.max_spread_pct,sp_last=l.max_spread_pct,
        sp_min=g.max_spread_pct.min(),tk_first=f.max_ticks,tk_last=l.max_ticks,tm_first=f.total_matched,tm_last=l.total_matched,
        fav_back_first=f.fav_back_price_1,fav_back_last=l.fav_back_price_1))
T9=pd.DataFrame(R); print(T9.to_string(index=False))
d=T9.dropna(subset=["sp_first","sp_last"])
print("mercados con spread en ambos extremos:",len(d)," spread mejora:",(d.sp_last<d.sp_first).sum()," empeora:",(d.sp_last>d.sp_first).sum()," igual:",(d.sp_last==d.sp_first).sum())
print("liq crece:",(T9.liq_last>T9.liq_first).sum()," de",len(T9),"; ratio mediano liq_last/liq_first:",round((T9.liq_last/T9.liq_first.replace(0,np.nan)).median(),2))
tmd=T9.dropna(subset=["tm_first","tm_last"]); print("total_matched crece:",(tmd.tm_last>tmd.tm_first).sum(),"de",len(tmd), " mediana tm_last:",tmd.tm_last.median())
print("movimiento del precio del favorito (|log(last/first)|) p50:",round(np.nanmedian(np.abs(np.log(T9.fav_back_last/T9.fav_back_first))),4))
# Hitos pareados: mercados con precio en 1h y close, 10m y close
print("\n### T10 — Hitos pareados dentro del mismo mercado")
mil=obs[obs.snapshot_label!="adaptive"].pivot_table(index="market_id",columns="snapshot_label",values=["has_prices","max_spread_pct","total_available"],aggfunc="first")
for x,y in [("6h","1h"),("1h","10m"),("10m","close"),("1h","close")]:
    both=mil[[("has_prices",x),("has_prices",y)]].dropna()
    n=len(both); px=int(both[("has_prices",x)].sum()); py=int(both[("has_prices",y)].sum())
    bb=mil[[("max_spread_pct",x),("max_spread_pct",y)]].dropna()
    print(f"{x}->{y}: mercados con ambos hitos={n}; con precio en {x}={px}, en {y}={py}; con spread en ambos={len(bb)}; spread p50 {x}={q(bb[('max_spread_pct',x)],50)} {y}={q(bb[('max_spread_pct',y)],50)}; mejora={(bb.iloc[:,1]<bb.iloc[:,0]).sum()}")
# precios que desaparecen
print("\n### T11 — Aparición de precio: minutos antes del inicio de la primera obs con precio (por mercado con precio)")
print(pm.max_mts_con_precio.describe(percentiles=[.25,.5,.75,.9]).round(1).to_string())
print("\n### T12 — Anomalías")
print(obs[(obs.inplay)|(obs.market_status!="OPEN")][["market_id","observed_at","snapshot_label","minutes_to_start","market_status","inplay","has_prices","max_spread_pct"]].to_string())
print("one_sided obs:", int(obs.one_sided.fillna(0).sum()), "| obs con precio y sin spread:", int((obs.has_prices & ~obs.has_spread).sum()))
print("obs con has_prices y n_runner_rows!=2:", int((obs.has_prices & (obs.n_runner_rows!=2)).sum()))
print("spread negativo o cero:", int((obs.max_spread_pct<=0).sum()))
print("Duplicados capture_key:", obs.duplicated(["market_id","capture_key"]).sum())
print("mercados catalogo sin observar:"); print(mk[~mk.market_id.isin(obs.market_id)][["market_id","market_start_time","first_seen_at","last_seen_at","competition_name","event_name"]].to_string())
print("\n### T13 — Observaciones por día (DB) y mercados que empiezan por día")
o=obs.assign(d=obs.observed_at.dt.tz_convert("Europe/Madrid").dt.date)
print(o.groupby("d").agg(obs=("observation_id","size"),mercados=("market_id","nunique"),adapt=("snapshot_label",lambda s:(s=="adaptive").sum()),con_precio=("has_prices","sum")).to_string())
ms=mk.assign(d=pd.to_datetime(mk.market_start_time,utc=True).dt.tz_convert("Europe/Madrid").dt.date).groupby("d").size(); print(ms.to_string())
print("\n### T14 — Gaps adaptive dentro de mercado: intervalo real vs esperado")
a2=obs[obs.snapshot_label=="adaptive"].sort_values(["market_id","observed_at"]).copy()
a2["dt"]=a2.groupby("market_id").observed_at.diff().dt.total_seconds()/60
a2["exp"]=a2.minutes_to_start.map(lambda m: 1 if m<=10 else 5 if m<=30 else 10 if m<=90 else 30)
a2["ratio"]=a2.dt/a2.exp
print(a2.groupby("exp").agg(n=("dt","count"),dt_p50=("dt","median"),dt_p90=("dt",lambda s:q(s,90)),dt_max=("dt","max"),pct_gt_1_5x=("ratio",lambda r:pct(r.dropna()>1.5))).round(2).to_string())
```

## `ticks.py`

```
import pandas as pd, numpy as np, sys
S=sys.argv[1]
o=pd.read_csv(f"{S}/observations.csv",dtype={"market_id":str}); rp=pd.read_csv(f"{S}/runner_price.csv")
LAD=[(1.01,2,0.01),(2,3,0.02),(3,4,0.05),(4,6,0.1),(6,10,0.2),(10,20,0.5),(20,30,1),(30,50,2),(50,100,5),(100,1000,10)]
lad=[]
for lo,hi,st in LAD:
    x=lo
    while x<hi-1e-9: lad.append(round(x,2)); x+=st
lad=np.array(lad+[1000.0]); t=lambda p:int(np.argmin(np.abs(lad-p)))
r=rp.dropna(subset=["back_price_1","lay_price_1"]).copy()
r["tk"]=[t(l)-t(b) for b,l in zip(r.back_price_1,r.lay_price_1)]
r["sp"]=(r.lay_price_1-r.back_price_1)/r.back_price_1*100
r["mid"]=(r.back_price_1+r.lay_price_1)/2
fav=r.sort_values("back_price_1").groupby("observation_id").first()
wst=r.groupby("observation_id").tk.max()
x=o.set_index("observation_id").join(fav[["tk","sp","back_price_1"]].rename(columns={"tk":"fav_tk","sp":"fav_sp","back_price_1":"fav_back"})).join(wst.rename("max_tk"))
x["grp"]=np.where(x.snapshot_label=="adaptive","adaptive","hitos")
def summ(d):
    f=d.fav_sp.dropna(); ft=d.fav_tk.dropna(); mt=d.max_tk.dropna()
    return pd.Series({"n_fav":len(f),"fav_sp_p25":f.quantile(.25),"fav_sp_p50":f.median(),"fav_sp_p75":f.quantile(.75),"fav_sp_p90":f.quantile(.9),
      "fav_le1%":100*(f<=1).mean(),"fav_le2%":100*(f<=2).mean(),"fav_le5%":100*(f<=5).mean(),"fav_le10%":100*(f<=10).mean(),"fav_gt10%":100*(f>10).mean(),
      "fav_tk_p50":ft.median(),"fav_le1tk":100*(ft<=1).mean(),"fav_le3tk":100*(ft<=3).mean(),"worst_le3tk":100*(mt<=3).mean(),"worst_le10tk":100*(mt<=10).mean()})
print("POR OBSERVACIÓN"); print(pd.concat({g:summ(d) for g,d in list(x.groupby("grp"))+[("ALL",x)]},axis=1).round(1).to_string())
pm=x.groupby("market_id").agg(fav_sp=("fav_sp","median"),fav_tk=("fav_tk","median"),max_tk=("max_tk","median")).dropna()
print("\nPOR MERCADO (mediana por mercado, n=%d)"%len(pm)); print(pm.describe(percentiles=[.25,.5,.75,.9]).round(1).to_string())
f=pm.fav_sp; print({k:round(100*v,1) for k,v in {"le1":(f<=1).mean(),"le2":(f<=2).mean(),"le5":(f<=5).mean(),"le10":(f<=10).mean(),"gt10":(f>10).mean()}.items()})
print("\ncuota favorito p50:", x.fav_back.median(), " p25/p75:", x.fav_back.quantile([.25,.75]).tolist())
print("\nmejor spread jamás visto por mercado (fav, %):"); print(x.groupby("market_id").fav_sp.min().dropna().sort_values().round(2).to_string())
```

## `check.py`

```
import pandas as pd, numpy as np, sys
S=sys.argv[1]
o=pd.read_csv(f"{S}/observations.csv",parse_dates=["observed_at","market_start_time"])
o=o.sort_values(["market_id","observed_at"])
o["implied_start"]=o.observed_at+pd.to_timedelta(o.minutes_to_start,unit="m")
o["shift_vs_final"]=(o.market_start_time-o.implied_start).dt.total_seconds()/60
g=o.groupby("market_id").agg(n=("observation_id","size"),start_changes=("implied_start",lambda s:(s.round("1min").diff().abs()>pd.Timedelta("2min")).sum()),
    shift_total=("implied_start",lambda s:(s.max()-s.min()).total_seconds()/60),n_adapt=("snapshot_label",lambda s:(s=="adaptive").sum()),comp=("competition_name","first"))
print("mercados con start desplazado (>2min):",(g.shift_total>2).sum(),"de",len(g))
print(g[g.shift_total>2].sort_values("shift_total",ascending=False).round(1).to_string())
print("\nadaptive obs en mercados con desplazamiento:", g[g.shift_total>2].n_adapt.sum(), "de", g.n_adapt.sum())
# hitos capturados respecto a un start que luego se movio
m=o[o.snapshot_label!="adaptive"]
print("\nhitos cuyo start implícito difiere del final >5min:")
print(m.assign(bad=m.shift_vs_final.abs()>5).groupby("snapshot_label").bad.agg(["sum","count"]).to_string())
print("minutos reales hasta el start final en 'close' desplazados:", m[(m.snapshot_label=="close")&(m.shift_vs_final.abs()>5)].eval("minutes_to_start+shift_vs_final").round(1).tolist())
# huecos incluyendo todas las obs
o["dt"]=o.groupby("market_id").observed_at.diff().dt.total_seconds()/60
a=o[o.snapshot_label=="adaptive"].copy()
a["exp"]=a.minutes_to_start.map(lambda m: 1 if m<=10 else 5 if m<=30 else 10 if m<=90 else 30)
a["r"]=a.dt/a.exp
print("\nintervalo real (incl. hitos) por cadencia esperada:")
print(a.groupby("exp").agg(n=("dt","count"),p50=("dt","median"),p90=("dt",lambda s:s.quantile(.9)),mx=("dt","max"),pct_gt1_5x=("r",lambda r:round(100*(r>1.5).mean(),1))).round(2).to_string())
print(a[a.r>3][["market_id","observed_at","minutes_to_start","dt","exp"]].round(2).to_string())
# eventos duplicados
e=o.groupby("market_id").agg(ev=("event_name","first"),start=("market_start_time","first")).reset_index()
d=e[e.duplicated("ev",keep=False)].sort_values("ev"); print("\npartidos con >1 market_id:"); print(d.to_string())
print("partidos únicos:", e.ev.nunique(), "de", len(e), "mercados observados")
# obs por mercado adaptive: distribución
print(g[g.n_adapt>0].n.describe().round(1).to_string())
```

## `logs.py`

```
import json, collections, pandas as pd, sys
S=sys.argv[1]
rows=[]
for src in [f"{S}/journal.txt", "logs/collector.log"]:
    for line in open(src, errors="replace"):
        line=line.strip()
        if not line.startswith("{"): continue
        try: d=json.loads(line)
        except Exception: continue
        d["_src"]=src.split("/")[-1]; rows.append(d)
df=pd.DataFrame(rows); df["ts"]=pd.to_datetime(df["timestamp"],utc=True)
for src,g in df.groupby("_src"):
    print("==",src, len(g), g.ts.min(), g.ts.max())
    print(g.groupby(["level","message"]).size().to_string())
j=df[df._src=="journal.txt"].copy()
end=pd.Timestamp("2026-09-27 06:49:00",tz="UTC"); start=end-pd.Timedelta(days=7)
w=j[(j.ts>=start)&(j.ts<=end)]
cyc=w[w.message.isin(["ciclo completado","ciclo fallido"])].sort_values("ts")
print("\n== ventana 7d", start, end)
print("ciclos:", len(cyc), "completados", (cyc.message=="ciclo completado").sum(), "fallidos", (cyc.message=="ciclo fallido").sum())
print("ciclos esperados a 60s:", int((end-start).total_seconds()//60))
gaps=cyc.ts.diff().dt.total_seconds()
print("intervalo entre ciclos (s):", gaps.describe(percentiles=[.5,.9,.99,.999]).to_string())
big=cyc.assign(gap=gaps)[gaps>90]
print("huecos >90s:", len(big)); print(big[["ts","gap","message"]].to_string())
print("max gap:", gaps.max())
# gap desde inicio de ventana
print("primer ciclo", cyc.ts.iloc[0], "ultimo", cyc.ts.iloc[-1])
# duracion estimada ciclos: time from gap excess
# errores por dia
e=w[w.level.isin(["ERROR","WARNING"])]
print(e.groupby([e.ts.dt.date,"level","message"]).size().to_string())
reauth=w[w.message.str.contains("sesion", na=False)]
print(reauth.groupby([reauth.ts.dt.date,"message"]).size().unstack().to_string())
ri=w[w.message=="sesion invalida, forzando reautenticacion"].ts.diff().dt.total_seconds()/60
print("minutos entre INVALID_SESSION:", ri.describe().to_string())
# failed cycles detail
for _,r in df[df.message=="ciclo fallido"].iterrows():
    exc=str(r.get("exception",""))
    print(r.ts, r._src, exc.strip().splitlines()[-1][:250] if exc else r.get("error"))
# cycles right after invalid session: was cycle completed?
inv=w[w.message=="sesion invalida, forzando reautenticacion"].ts
nxt=[cyc[cyc.ts>=t].iloc[0] for t in inv if (cyc.ts>=t).any()]
print("ciclo tras INVALID_SESSION:", collections.Counter(n.message for n in nxt))
# totals written
cc=w[w.message=="ciclo completado"]
for k in ["written","with_prices","without_prices","due","markets"]:
    if k in cc: print(k, cc[k].sum() if k!="markets" else cc[k].describe().to_string())
# hourly written
h=cc.set_index("ts")["written"].resample("1D").agg(["sum","count"]); print(h.to_string())
```
