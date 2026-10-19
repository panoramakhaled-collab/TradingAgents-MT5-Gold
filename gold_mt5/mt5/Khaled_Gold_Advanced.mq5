// Analysis overlay only: no trade, network, DLL or shell operations.
#property version "2.00"
#property indicator_chart_window
#property indicator_buffers 2
#property indicator_plots 2
#property indicator_type1 DRAW_LINE
#property indicator_color1 clrDarkOrange
#property indicator_label1 "Tick-weighted price proxy"
#property indicator_type2 DRAW_LINE
#property indicator_color2 clrRoyalBlue
#property indicator_label2 "Trade-volume VWAP (only if available)"

input int PivotStrength=2;
input int ScanBars=300;
input int GapMaxAge=36;
input int MaxGaps=6;
input double MinGapATR=0.05;
input double DisplacementBodyATR=0.8;
input bool ShowStructure=true;
input bool ShowLiquidity=true;
input bool ShowSwingFib=true;
input bool ShowGaps=true;
input bool ShowActivityProxy=true;
input bool ShowActivityProfile=true;

double TickProxy[],RealVWAP[];
string P="KhaledAdvanced_";
datetime LastClosed=0;
struct Gap {int side;double lower;double upper;int formed;int status;datetime born;};

void Text(string id,datetime at,double price,string label,color c)
{
 string n=P+id;
 ObjectCreate(0,n,OBJ_TEXT,0,at,price);
 ObjectMove(0,n,0,at,price);
 ObjectSetString(0,n,OBJPROP_TEXT,label);
 ObjectSetInteger(0,n,OBJPROP_COLOR,c);
 ObjectSetInteger(0,n,OBJPROP_FONTSIZE,7);
 ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
}
void Level(string id,double price,string label,color c,int style=STYLE_DOT)
{
 string n=P+id;
 ObjectCreate(0,n,OBJ_HLINE,0,0,price);
 ObjectSetDouble(0,n,OBJPROP_PRICE,price);
 ObjectSetString(0,n,OBJPROP_TEXT,label+" "+DoubleToString(price,_Digits));
 ObjectSetInteger(0,n,OBJPROP_COLOR,c);
 ObjectSetInteger(0,n,OBJPROP_STYLE,style);
 ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
 Text(id+"_label",iTime(_Symbol,_Period,0),price,label+" "+DoubleToString(price,_Digits),c);
 ObjectSetInteger(0,P+id+"_label",OBJPROP_ANCHOR,ANCHOR_RIGHT);
}
void Label(string id,int y,string value,color c=clrSilver)
{
 string n=P+id;
 ObjectCreate(0,n,OBJ_LABEL,0,0,0);
 ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER_LEFT_UPPER);
 ObjectSetInteger(0,n,OBJPROP_XDISTANCE,12);
 ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y);
 ObjectSetInteger(0,n,OBJPROP_FONTSIZE,8);
 ObjectSetInteger(0,n,OBJPROP_COLOR,c);
 ObjectSetString(0,n,OBJPROP_TEXT,value);
 ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
}
string FrameName()
{
 string s=EnumToString(_Period);
 StringReplace(s,"PERIOD_","");
 return s;
}
void DrawPlan()
{
 ObjectsDeleteAll(0,P+"plan_");
 string file="KhaledGoldAdvanced_plan_"+FrameName()+".csv";
 int h=FileOpen(file,FILE_READ|FILE_CSV|FILE_ANSI,',');
 if(h==INVALID_HANDLE){Label("model",118,"Model: WAIT - no verified setup file");return;}
 string status="WAIT",asof="",condition="",expiry="";
 double entry=0,sl=0,tp1=0,tp2=0,tp3=0;
 while(!FileIsEnding(h))
 {
  string key=FileReadString(h),value=FileReadString(h);
  if(key=="status")status=value;
  if(key=="as_of_server")asof=value;
  if(key=="condition")condition=value;
  if(key=="expires_server")expiry=value;
  if(key=="entry")entry=StringToDouble(value);
  if(key=="stop")sl=StringToDouble(value);
  if(key=="tp1")tp1=StringToDouble(value);
  if(key=="tp2")tp2=StringToDouble(value);
  if(key=="tp3")tp3=StringToDouble(value);
 }
 FileClose(h);
 datetime expires=StringToTime(expiry);
 if(expires>0 && TimeTradeServer()>expires)status="EXPIRED";
 Label("model",118,"Model "+status+" | verified "+asof,clrLightSteelBlue);
 if(status=="WAIT")condition="Waiting: sweep -> structure break -> first FVG rejection";
 Label("condition",144,StringSubstr(condition,0,76),clrLightSteelBlue);
 string names[5]={"Entry","SL","TP1","TP2","TP3"};
 double values[5];values[0]=entry;values[1]=sl;values[2]=tp1;values[3]=tp2;values[4]=tp3;
 if(status=="BUY" || status=="SELL" || status=="CONDITIONAL")
  for(int i=0;i<5;i++)if(values[i]>0)Level("plan_"+names[i],values[i],names[i],i==1?clrCrimson:clrDarkSlateBlue,STYLE_DASH);
}

int OnInit()
{
 SetIndexBuffer(0,TickProxy,INDICATOR_DATA);
 SetIndexBuffer(1,RealVWAP,INDICATOR_DATA);
 ArraySetAsSeries(TickProxy,true);ArraySetAsSeries(RealVWAP,true);
 IndicatorSetString(INDICATOR_SHORTNAME,"Gold Advanced Structure v2");
 ObjectsDeleteAll(0,P);
 return INIT_SUCCEEDED;
}
void OnDeinit(const int reason){ObjectsDeleteAll(0,P);}

int OnCalculate(const int total,const int prev,const datetime &time[],const double &open[],
 const double &high[],const double &low[],const double &close[],const long &ticks[],
 const long &volume[],const int &spread[])
{
 if(total<220)return 0;
 ArraySetAsSeries(time,true);ArraySetAsSeries(open,true);ArraySetAsSeries(high,true);
 ArraySetAsSeries(low,true);ArraySetAsSeries(close,true);ArraySetAsSeries(ticks,true);
 ArraySetAsSeries(volume,true);
 if(prev==0){ArrayInitialize(TickProxy,EMPTY_VALUE);ArrayInitialize(RealVWAP,EMPTY_VALUE);}
 if(LastClosed==time[1] && prev>0){DrawPlan();return total;}
 LastClosed=time[1];ObjectsDeleteAll(0,P);
 int oldest=MathMin(ScanBars+200,total-2*PivotStrength-2);
 double atr[];ArrayResize(atr,oldest+2);
 atr[oldest]=high[oldest]-low[oldest];
 for(int s=oldest-1;s>=0;s--)
 {
  double tr=MathMax(high[s]-low[s],MathMax(MathAbs(high[s]-close[s+1]),MathAbs(low[s]-close[s+1])));
  atr[s]=(13*atr[s+1]+tr)/14;
 }
 int day=-1;double tpv=0,tv=0,rpv=0,rv=0;
 for(int s=oldest;s>=1;s--)
 {
  MqlDateTime dt;TimeToStruct(time[s],dt);int d=dt.year*10000+dt.mon*100+dt.day;
  if(d!=day){day=d;tpv=0;tv=0;rpv=0;rv=0;}
  double price=(high[s]+low[s]+close[s])/3;
  tpv+=price*(double)ticks[s];tv+=(double)ticks[s];rpv+=price*(double)volume[s];rv+=(double)volume[s];
  TickProxy[s]=ShowActivityProxy && tv>0?tpv/tv:EMPTY_VALUE;
  RealVWAP[s]=rv>0?rpv/rv:EMPTY_VALUE;
 }
 TickProxy[0]=EMPTY_VALUE;RealVWAP[0]=EMPTY_VALUE;
 double ph=0,pl=0;int phbar=-1,plbar=-1,lastHB=-1,lastLB=-1,direction=0;
 Gap gaps[];ArrayResize(gaps,0);
 for(int s=MathMin(ScanBars,oldest-2*PivotStrength);s>=1;s--)
 {
  int j=s+PivotStrength;bool isH=true,isL=true;
  for(int k=j-PivotStrength;k<=j+PivotStrength;k++)if(k!=j)
  {if(high[j]<=high[k])isH=false;if(low[j]>=low[k])isL=false;}
  if(isH){ph=high[j];phbar=j;if(ShowStructure && s<=24){Text("PH"+IntegerToString(j),time[j],ph+atr[s]*0.2,"PH",clrIndianRed);ObjectSetString(0,P+"PH"+IntegerToString(j),OBJPROP_TOOLTIP,"Pivot confirmed "+TimeToString(time[s],TIME_DATE|TIME_MINUTES));}}
  if(isL){pl=low[j];plbar=j;if(ShowStructure && s<=24){Text("PL"+IntegerToString(j),time[j],pl-atr[s]*0.2,"PL",clrSeaGreen);ObjectSetString(0,P+"PL"+IntegerToString(j),OBJPROP_TOOLTIP,"Pivot confirmed "+TimeToString(time[s],TIME_DATE|TIME_MINUTES));}}
  int di=iBarShift(_Symbol,PERIOD_D1,time[s],false);
  double pdh=di>=0?iHigh(_Symbol,PERIOD_D1,di+1):0;
  double pdl=di>=0?iLow(_Symbol,PERIOD_D1,di+1):0;
  bool sweepH=(ph>0 && high[s]>ph && close[s]<ph)||(pdh>0 && high[s]>pdh && close[s]<pdh);
  bool sweepL=(pl>0 && low[s]<pl && close[s]>pl)||(pdl>0 && low[s]<pdl && close[s]>pdl);
  if(ShowLiquidity && s<=40 && sweepH && !sweepL)Text("SH"+IntegerToString(phbar),time[s],high[s]+atr[s]*0.5,"Sweep H",clrCrimson);
  if(ShowLiquidity && s<=40 && sweepL && !sweepH)Text("SL"+IntegerToString(plbar),time[s],low[s]-atr[s]*0.5,"Sweep L",clrGreen);
  if(ph>0 && close[s]>ph && close[s+1]<=ph && lastHB!=phbar)
  {if(ShowStructure && s<=60)Text("BH"+IntegerToString(s),time[s],high[s]+atr[s]*0.35,direction==-1?"CHOCH up":"BOS up",clrGreen);direction=1;lastHB=phbar;}
  if(pl>0 && close[s]<pl && close[s+1]>=pl && lastLB!=plbar)
  {if(ShowStructure && s<=60)Text("BL"+IntegerToString(s),time[s],low[s]-atr[s]*0.35,direction==1?"CHOCH down":"BOS down",clrCrimson);direction=-1;lastLB=plbar;}
  for(int g=0;g<ArraySize(gaps);g++)if(gaps[g].status<=1)
  {
   if(gaps[g].formed-s>GapMaxAge || time[s]-gaps[g].born>GapMaxAge*PeriodSeconds()){gaps[g].status=4;continue;}
   bool invalid=gaps[g].side==1?close[s]<gaps[g].lower:close[s]>gaps[g].upper;
   bool filled=gaps[g].side==1?low[s]<=gaps[g].lower:high[s]>=gaps[g].upper;
   bool touched=low[s]<=gaps[g].upper && high[s]>=gaps[g].lower;
   if(invalid)gaps[g].status=3;
   else if(filled)gaps[g].status=2;
   else if(touched)gaps[g].status=1;
  }
  int side=low[s]>high[s+2]?1:high[s]<low[s+2]?-1:0;
  if(side!=0 && time[s]-time[s+1]==PeriodSeconds() && time[s+1]-time[s+2]==PeriodSeconds())
  {
   double lo=side==1?high[s+2]:high[s],hi=side==1?low[s]:low[s+2];
   bool body=(MathAbs(close[s+1]-open[s+1])>=DisplacementBodyATR*atr[s+1]) && side*(close[s+1]-open[s+1])>0;
   if(hi-lo>=MinGapATR*atr[s] && body)
   {int n=ArraySize(gaps);ArrayResize(gaps,n+1);gaps[n].side=side;gaps[n].lower=lo;gaps[n].upper=hi;gaps[n].formed=s;gaps[n].status=0;gaps[n].born=time[s];}
  }
 }
 if(ShowGaps)
 {
  int shown=0;
  for(int g=ArraySize(gaps)-1;g>=0 && shown<MaxGaps;g--)
  {
   if(gaps[g].status>1)continue;
   string id=P+"gap"+IntegerToString(g);
   ObjectCreate(0,id,OBJ_RECTANGLE,0,gaps[g].born,gaps[g].lower,time[0]+PeriodSeconds()*3,gaps[g].upper);
   color c=gaps[g].side==1?clrPaleGreen:clrMistyRose;
   ObjectSetInteger(0,id,OBJPROP_COLOR,c);ObjectSetInteger(0,id,OBJPROP_FILL,true);
   ObjectSetInteger(0,id,OBJPROP_BACK,true);ObjectSetInteger(0,id,OBJPROP_SELECTABLE,false);
   Text("gaplabel"+IntegerToString(g),time[0],gaps[g].upper,"FVG "+(gaps[g].status==0?"fresh":"touched"),clrSlateGray);shown++;
  }
 }
 if(ShowSwingFib && ph>pl && pl>0)
 {
  double ratios[7]={0,0.236,0.382,0.5,0.618,0.786,1};
  for(int k=0;k<7;k++)Level("fib"+IntegerToString(k),pl+(ph-pl)*ratios[k],"Fib "+DoubleToString(ratios[k]*100,1)+"%",clrSilver);
 }
 if(ShowLiquidity)
 {double h=iHigh(_Symbol,PERIOD_D1,1),l=iLow(_Symbol,PERIOD_D1,1);if(h>0)Level("PDH",h,"Previous day high",clrDarkGoldenrod);if(l>0)Level("PDL",l,"Previous day low",clrDarkGoldenrod);}
 if(ShowActivityProfile)
 {
  int count=MathMin(200,total-1);double lo=low[1],hi=high[1];
  for(int i=2;i<=count;i++){lo=MathMin(lo,low[i]);hi=MathMax(hi,high[i]);}
  if(hi>lo)
  {
   double weights[24];ArrayInitialize(weights,0);double width=(hi-lo)/24,totalWeight=0;
   for(int i=1;i<=count;i++){int bin=(int)(((high[i]+low[i]+close[i])/3-lo)/width);bin=MathMax(0,MathMin(23,bin));weights[bin]+=(double)ticks[i];totalWeight+=(double)ticks[i];}
   int poc=0;for(int i=1;i<24;i++)if(weights[i]>weights[poc])poc=i;
   int left=poc,right=poc;double accumulated=weights[poc];
   while(totalWeight>0 && accumulated<0.7*totalWeight && (left>0 || right<23))
   {double l=left>0?weights[left-1]:-1,r=right<23?weights[right+1]:-1;if(r>l){right++;accumulated+=weights[right];}else{left--;accumulated+=weights[left];}}
   if(totalWeight>0){Level("activityPOC",lo+(poc+0.5)*width,"Tick activity POC",clrDarkOrange);Level("activityVAL",lo+left*width,"Tick activity VAL",clrDarkOrange);Level("activityVAH",lo+(right+1)*width,"Tick activity VAH",clrDarkOrange);}
  }
 }
 ObjectCreate(0,P+"panel",OBJ_RECTANGLE_LABEL,0,0,0);
 ObjectSetInteger(0,P+"panel",OBJPROP_XDISTANCE,6);ObjectSetInteger(0,P+"panel",OBJPROP_YDISTANCE,18);
 ObjectSetInteger(0,P+"panel",OBJPROP_XSIZE,730);ObjectSetInteger(0,P+"panel",OBJPROP_YSIZE,170);
 ObjectSetInteger(0,P+"panel",OBJPROP_BGCOLOR,C'24,24,24');ObjectSetInteger(0,P+"panel",OBJPROP_BORDER_TYPE,BORDER_FLAT);
 ObjectSetInteger(0,P+"panel",OBJPROP_BACK,false);ObjectSetInteger(0,P+"panel",OBJPROP_SELECTABLE,false);
 Label("title",20,"Gold Advanced v2 | "+FrameName()+" | completed candles",clrLightSteelBlue);
 Label("structure",46,"Structure: "+(direction==1?"UP":direction==-1?"DOWN":"unconfirmed")+" | ATR14 "+DoubleToString(atr[1],2));
 Label("clock",72,"Last closed: "+TimeToString(time[1],TIME_DATE|TIME_MINUTES)+" server time");
 Label("volume",96,rv>0?"Blue: actual trade-volume VWAP":"Orange: tick activity proxy; real delta unavailable");
 DrawPlan();ChartRedraw(0);return total;
}
