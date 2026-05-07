//+------------------------------------------------------------------+
//| XAUUSD Price Action Confluence Expert Advisor for MetaTrader 5   |
//| Strategy: M1 EMA trend filter + price action signal confluence   |
//+------------------------------------------------------------------+
#property copyright "Open-source educational Expert Advisor"
#property version   "1.00"
#property strict
#property description "Trades XAUUSD M1 when at least three price action signals align with the EMA trend."

#include <Trade/Trade.mqh>

enum TradeDirection
{
   DIR_SELL = -1,
   DIR_NONE = 0,
   DIR_BUY  = 1
};

input string          InpTradeSymbol              = "XAUUSD";
input ENUM_TIMEFRAMES InpSignalTimeframe          = PERIOD_M1;
input long            InpMagicNumber              = 2026050701;
input bool            InpEnableAutoTrading        = true;
input int             InpMaxOpenTrades            = 2;
input int             InpDeviationPoints          = 30;

input int             InpEMAPeriod                = 20;
input bool            InpRequireEMASlope          = true;
input int             InpMinimumSignals           = 3;

input bool            InpUseAccountRisk           = true;
input double          InpRiskPercent              = 1.0;
input double          InpFixedLot                 = 0.01;
input int             InpSwingLookbackBars        = 8;
input int             InpSLBufferPoints           = 80;
input double          InpTakeProfitToSLRatio      = 1.5;

input double          InpPinShadowToBodyRatio     = 2.0;
input double          InpPinMaxBodyPercent        = 35.0;
input double          InpPinOppositeShadowPercent = 30.0;
input double          InpEngulfBodyRatio          = 1.0;
input int             InpFakeBreakoutLookback     = 25;
input int             InpFakeBreakoutBufferPoints = 30;
input int             InpPullbackCandles          = 2;
input int             InpDynamicSRLookback        = 30;
input int             InpSRProximityPoints        = 120;
input bool            InpUseDynamicSR             = true;
input string          InpSupportLevels            = "";
input string          InpResistanceLevels         = "";

input bool            InpShowChartArrows          = true;
input bool            InpEnablePopupAlerts        = true;
input bool            InpEnableSoundAlerts        = false;
input string          InpSoundFile                = "alert.wav";

struct SignalResult
{
   int    direction;
   int    score;
   string reasons;
};

CTrade  trade;
int     g_emaHandle = INVALID_HANDLE;
string  g_symbol = "";
datetime g_lastBarTime = 0;

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   g_symbol = (StringLen(InpTradeSymbol) == 0 ? _Symbol : InpTradeSymbol);

   if(!SymbolSelect(g_symbol, true))
   {
      PrintFormat("Failed to select symbol %s. Check that the broker offers this symbol.", g_symbol);
      return INIT_FAILED;
   }

   g_emaHandle = iMA(g_symbol, InpSignalTimeframe, InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(g_emaHandle == INVALID_HANDLE)
   {
      PrintFormat("Failed to create EMA handle for %s. Error: %d", g_symbol, GetLastError());
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpDeviationPoints);
   trade.SetTypeFillingBySymbol(g_symbol);

   PrintFormat("XAUUSD Price Action Confluence EA initialized on %s %s.", g_symbol, EnumToString(InpSignalTimeframe));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_emaHandle != INVALID_HANDLE)
      IndicatorRelease(g_emaHandle);
}

//+------------------------------------------------------------------+
//| Main tick handler                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewBar())
      return;

   MqlRates rates[];
   double ema[];
   if(!LoadMarketData(rates, ema))
      return;

   SignalResult signal = EvaluateSignal(rates, ema);
   int minSignals = ClampInt(InpMinimumSignals, 1, 5);

   if(signal.direction == DIR_NONE || signal.score < minSignals)
      return;

   DrawSignalMarker(signal.direction, rates[1], signal);
   SendSignalAlert(signal);

   if(!InpEnableAutoTrading)
   {
      PrintFormat("%s signal detected but auto trading is disabled. Signals: %d (%s)",
                  DirectionText(signal.direction), signal.score, signal.reasons);
      return;
   }

   if(CountOpenTrades() >= InpMaxOpenTrades)
   {
      PrintFormat("Signal skipped because max open trades (%d) is already reached.", InpMaxOpenTrades);
      return;
   }

   OpenTrade(signal, rates);
}

//+------------------------------------------------------------------+
//| Data loading                                                     |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime barTimes[];
   ArraySetAsSeries(barTimes, true);

   if(CopyTime(g_symbol, InpSignalTimeframe, 0, 2, barTimes) < 2)
      return false;

   if(barTimes[0] == g_lastBarTime)
      return false;

   g_lastBarTime = barTimes[0];
   return true;
}

bool LoadMarketData(MqlRates &rates[], double &ema[])
{
   int longestLookback = MaxInt(InpDynamicSRLookback, MaxInt(InpFakeBreakoutLookback, InpSwingLookbackBars));
   int requiredBars = MaxInt(80, longestLookback + 20);

   ArraySetAsSeries(rates, true);
   int copiedRates = CopyRates(g_symbol, InpSignalTimeframe, 0, requiredBars, rates);
   if(copiedRates < 20)
   {
      PrintFormat("Not enough bars loaded for %s. Loaded: %d", g_symbol, copiedRates);
      return false;
   }

   ArraySetAsSeries(ema, true);
   int copiedEma = CopyBuffer(g_emaHandle, 0, 0, copiedRates, ema);
   if(copiedEma < copiedRates)
   {
      PrintFormat("Not enough EMA data. Loaded: %d of %d. Error: %d", copiedEma, copiedRates, GetLastError());
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Signal engine                                                    |
//+------------------------------------------------------------------+
int MinInt(const int first, const int second)
{
   return (first < second ? first : second);
}

int MaxInt(const int first, const int second)
{
   return (first > second ? first : second);
}

int ClampInt(const int value, const int minValue, const int maxValue)
{
   return MaxInt(minValue, MinInt(maxValue, value));
}

SignalResult EvaluateSignal(MqlRates &rates[], double &ema[])
{
   SignalResult result;
   result.direction = DIR_NONE;
   result.score = 0;
   result.reasons = "";

   int trendDirection = GetTrendDirection(rates, ema);
   if(trendDirection == DIR_NONE)
      return result;

   result.direction = trendDirection;
   double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);

   if(IsPinBar(rates[1], trendDirection, point))
   {
      result.score++;
      AddReason(result.reasons, "pin bar");
   }

   if(IsEngulfing(rates[1], rates[2], trendDirection))
   {
      result.score++;
      AddReason(result.reasons, "engulfing");
   }

   if(IsFakeBreakout(rates, trendDirection, point))
   {
      result.score++;
      AddReason(result.reasons, "fake breakout");
   }

   if(IsPullback(rates, trendDirection))
   {
      result.score++;
      AddReason(result.reasons, "pullback");
   }

   if(IsSupportResistanceSignal(rates, trendDirection, point))
   {
      result.score++;
      AddReason(result.reasons, "support/resistance");
   }

   return result;
}

int GetTrendDirection(MqlRates &rates[], double &ema[])
{
   bool emaRising = (ema[1] >= ema[2]);
   bool emaFalling = (ema[1] <= ema[2]);

   if(rates[1].close > ema[1] && (!InpRequireEMASlope || emaRising))
      return DIR_BUY;

   if(rates[1].close < ema[1] && (!InpRequireEMASlope || emaFalling))
      return DIR_SELL;

   return DIR_NONE;
}

bool IsPinBar(const MqlRates &bar, const int direction, const double point)
{
   double range = bar.high - bar.low;
   if(range <= point)
      return false;

   double body = MathMax(MathAbs(bar.close - bar.open), point);
   double upperShadow = bar.high - MathMax(bar.open, bar.close);
   double lowerShadow = MathMin(bar.open, bar.close) - bar.low;
   double bodyPercent = (body / range) * 100.0;
   double oppositeLimit = (range * InpPinOppositeShadowPercent) / 100.0;

   if(bodyPercent > InpPinMaxBodyPercent)
      return false;

   if(direction == DIR_BUY)
      return (lowerShadow >= body * InpPinShadowToBodyRatio && upperShadow <= oppositeLimit && bar.close > bar.open);

   if(direction == DIR_SELL)
      return (upperShadow >= body * InpPinShadowToBodyRatio && lowerShadow <= oppositeLimit && bar.close < bar.open);

   return false;
}

bool IsEngulfing(const MqlRates &current, const MqlRates &previous, const int direction)
{
   double currentBody = MathAbs(current.close - current.open);
   double previousBody = MathAbs(previous.close - previous.open);
   if(previousBody <= 0.0 || currentBody < previousBody * InpEngulfBodyRatio)
      return false;

   if(direction == DIR_BUY)
   {
      return (previous.close < previous.open &&
              current.close > current.open &&
              current.open <= previous.close &&
              current.close >= previous.open);
   }

   if(direction == DIR_SELL)
   {
      return (previous.close > previous.open &&
              current.close < current.open &&
              current.open >= previous.close &&
              current.close <= previous.open);
   }

   return false;
}

bool IsFakeBreakout(MqlRates &rates[], const int direction, const double point)
{
   int lookback = MaxInt(5, InpFakeBreakoutLookback);
   double support = LowestLow(rates, 2, lookback);
   double resistance = HighestHigh(rates, 2, lookback);
   double buffer = InpFakeBreakoutBufferPoints * point;

   if(direction == DIR_BUY)
      return (rates[1].low < support - buffer && rates[1].close > support);

   if(direction == DIR_SELL)
      return (rates[1].high > resistance + buffer && rates[1].close < resistance);

   return false;
}

bool IsPullback(MqlRates &rates[], const int direction)
{
   int pullbackBars = ClampInt(InpPullbackCandles, 2, 3);

   if(direction == DIR_BUY)
   {
      if(rates[1].close <= rates[1].open || rates[1].close <= rates[2].high)
         return false;

      for(int i = 2; i <= pullbackBars + 1; i++)
      {
         bool bearishRetracement = (rates[i].close < rates[i].open || rates[i].close < rates[i + 1].close);
         if(!bearishRetracement)
            return false;
      }
      return true;
   }

   if(direction == DIR_SELL)
   {
      if(rates[1].close >= rates[1].open || rates[1].close >= rates[2].low)
         return false;

      for(int i = 2; i <= pullbackBars + 1; i++)
      {
         bool bullishRetracement = (rates[i].close > rates[i].open || rates[i].close > rates[i + 1].close);
         if(!bullishRetracement)
            return false;
      }
      return true;
   }

   return false;
}

bool IsSupportResistanceSignal(MqlRates &rates[], const int direction, const double point)
{
   double proximity = InpSRProximityPoints * point;
   bool dynamicSignal = false;

   if(InpUseDynamicSR)
   {
      int lookback = MaxInt(5, InpDynamicSRLookback);
      double support = LowestLow(rates, 2, lookback);
      double resistance = HighestHigh(rates, 2, lookback);

      if(direction == DIR_BUY)
         dynamicSignal = (rates[1].low <= support + proximity && rates[1].close > support && rates[1].close > rates[1].open);

      if(direction == DIR_SELL)
         dynamicSignal = (rates[1].high >= resistance - proximity && rates[1].close < resistance && rates[1].close < rates[1].open);
   }

   return (dynamicSignal || IsUserLevelSignal(rates[1], direction, proximity));
}

bool IsUserLevelSignal(const MqlRates &bar, const int direction, const double proximity)
{
   if(direction == DIR_BUY)
      return IsNearAnyLevel(InpSupportLevels, bar.low, bar.close, proximity, true);

   if(direction == DIR_SELL)
      return IsNearAnyLevel(InpResistanceLevels, bar.high, bar.close, proximity, false);

   return false;
}

bool IsNearAnyLevel(const string levels, const double extremePrice, const double closePrice, const double proximity, const bool supportMode)
{
   if(StringLen(levels) == 0)
      return false;

   string normalized = levels;
   StringReplace(normalized, ";", ",");
   StringReplace(normalized, " ", "");
   StringReplace(normalized, "\t", "");

   string parts[];
   ushort comma = (ushort)StringGetCharacter(",", 0);
   int count = StringSplit(normalized, comma, parts);
   for(int i = 0; i < count; i++)
   {
      if(StringLen(parts[i]) == 0)
         continue;

      double level = StringToDouble(parts[i]);
      if(level <= 0.0)
         continue;

      if(supportMode)
      {
         if(extremePrice <= level + proximity && closePrice > level)
            return true;
      }
      else
      {
         if(extremePrice >= level - proximity && closePrice < level)
            return true;
      }
   }

   return false;
}

double LowestLow(MqlRates &rates[], const int startIndex, const int count)
{
   int total = ArraySize(rates);
   int endIndex = MinInt(total - 1, startIndex + count - 1);
   double value = rates[startIndex].low;

   for(int i = startIndex + 1; i <= endIndex; i++)
      value = MathMin(value, rates[i].low);

   return value;
}

double HighestHigh(MqlRates &rates[], const int startIndex, const int count)
{
   int total = ArraySize(rates);
   int endIndex = MinInt(total - 1, startIndex + count - 1);
   double value = rates[startIndex].high;

   for(int i = startIndex + 1; i <= endIndex; i++)
      value = MathMax(value, rates[i].high);

   return value;
}

void AddReason(string &reasons, const string reason)
{
   if(StringLen(reasons) > 0)
      reasons += ", ";

   reasons += reason;
}

//+------------------------------------------------------------------+
//| Trade execution and risk management                              |
//+------------------------------------------------------------------+
void OpenTrade(const SignalResult &signal, MqlRates &rates[])
{
   MqlTick tick;
   if(!SymbolInfoTick(g_symbol, tick))
   {
      PrintFormat("Cannot read tick data for %s. Error: %d", g_symbol, GetLastError());
      return;
   }

   double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   double entryPrice = (signal.direction == DIR_BUY ? tick.ask : tick.bid);
   double stopLoss = CalculateStopLoss(signal.direction, rates, entryPrice, point);

   if(stopLoss <= 0.0)
      return;

   double minStopDistance = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double riskDistance = MathAbs(entryPrice - stopLoss);
   if(riskDistance < minStopDistance)
   {
      stopLoss = (signal.direction == DIR_BUY ? entryPrice - minStopDistance : entryPrice + minStopDistance);
      riskDistance = MathAbs(entryPrice - stopLoss);
   }

   if(riskDistance <= point)
   {
      Print("Trade skipped because stop loss distance is too small.");
      return;
   }

   double takeProfit = (signal.direction == DIR_BUY ?
                        entryPrice + riskDistance * InpTakeProfitToSLRatio :
                        entryPrice - riskDistance * InpTakeProfitToSLRatio);

   stopLoss = NormalizeDouble(stopLoss, digits);
   takeProfit = NormalizeDouble(takeProfit, digits);
   double lotSize = CalculateLotSize(entryPrice, stopLoss);

   if(lotSize <= 0.0)
   {
      Print("Trade skipped because lot size calculation returned zero.");
      return;
   }

   string comment = StringFormat("PA confluence %d: %s", signal.score, signal.reasons);
   bool placed = false;

   if(signal.direction == DIR_BUY)
      placed = trade.Buy(lotSize, g_symbol, 0.0, stopLoss, takeProfit, comment);
   else if(signal.direction == DIR_SELL)
      placed = trade.Sell(lotSize, g_symbol, 0.0, stopLoss, takeProfit, comment);

   if(!placed)
   {
      PrintFormat("Order failed. Retcode: %d, description: %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return;
   }

   PrintFormat("%s trade opened: lot %.2f, SL %.2f, TP %.2f, signals %d (%s)",
               DirectionText(signal.direction), lotSize, stopLoss, takeProfit, signal.score, signal.reasons);
}

double CalculateStopLoss(const int direction, MqlRates &rates[], const double entryPrice, const double point)
{
   int lookback = MaxInt(2, InpSwingLookbackBars);
   double buffer = InpSLBufferPoints * point;

   if(direction == DIR_BUY)
   {
      double swingLow = LowestLow(rates, 1, lookback);
      double stopLoss = swingLow - buffer;
      if(stopLoss >= entryPrice)
      {
         PrintFormat("Invalid buy stop loss %.5f for entry %.5f.", stopLoss, entryPrice);
         return 0.0;
      }
      return stopLoss;
   }

   if(direction == DIR_SELL)
   {
      double swingHigh = HighestHigh(rates, 1, lookback);
      double stopLoss = swingHigh + buffer;
      if(stopLoss <= entryPrice)
      {
         PrintFormat("Invalid sell stop loss %.5f for entry %.5f.", stopLoss, entryPrice);
         return 0.0;
      }
      return stopLoss;
   }

   return 0.0;
}

double CalculateLotSize(const double entryPrice, const double stopLoss)
{
   if(!InpUseAccountRisk)
      return NormalizeVolume(InpFixedLot);

   double tickSize = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0.0 || tickValue <= 0.0)
   {
      Print("Broker did not provide tick size/value. Falling back to fixed lot.");
      return NormalizeVolume(InpFixedLot);
   }

   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * (MathMax(0.01, InpRiskPercent) / 100.0);
   double lossPerLot = (MathAbs(entryPrice - stopLoss) / tickSize) * tickValue;
   if(lossPerLot <= 0.0)
      return NormalizeVolume(InpFixedLot);

   return NormalizeVolume(riskMoney / lossPerLot);
}

double NormalizeVolume(const double requestedLot)
{
   double minLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);

   if(lotStep <= 0.0)
      lotStep = minLot;

   double lot = MathMax(minLot, MathMin(maxLot, requestedLot));
   lot = minLot + MathFloor((lot - minLot) / lotStep) * lotStep;

   int volumeDigits = 0;
   double step = lotStep;
   while(volumeDigits < 8 && MathAbs(step - MathRound(step)) > 0.00000001)
   {
      step *= 10.0;
      volumeDigits++;
   }

   return NormalizeDouble(lot, volumeDigits);
}

int CountOpenTrades()
{
   int count = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      if(PositionGetString(POSITION_SYMBOL) == g_symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         count++;
      }
   }

   return count;
}

//+------------------------------------------------------------------+
//| Chart markers and alerts                                         |
//+------------------------------------------------------------------+
void DrawSignalMarker(const int direction, const MqlRates &bar, const SignalResult &signal)
{
   if(!InpShowChartArrows)
      return;

   double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   double markerPrice = (direction == DIR_BUY ? bar.low - 10.0 * point : bar.high + 10.0 * point);
   string name = "XAUUSD_PA_" + DirectionText(direction) + "_" + TimeToString(bar.time, TIME_DATE | TIME_MINUTES);
   StringReplace(name, ".", "");
   StringReplace(name, ":", "");
   StringReplace(name, " ", "_");

   if(ObjectFind(0, name) >= 0)
      return;

   ENUM_OBJECT arrowType = (direction == DIR_BUY ? OBJ_ARROW_BUY : OBJ_ARROW_SELL);
   color arrowColor = (direction == DIR_BUY ? clrLime : clrRed);

   if(ObjectCreate(0, name, arrowType, 0, bar.time, markerPrice))
   {
      ObjectSetInteger(0, name, OBJPROP_COLOR, arrowColor);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   }

   string labelName = name + "_label";
   if(ObjectCreate(0, labelName, OBJ_TEXT, 0, bar.time, markerPrice))
   {
      ObjectSetString(0, labelName, OBJPROP_TEXT,
                      StringFormat("%s %d/5\n%s", DirectionText(direction), signal.score, signal.reasons));
      ObjectSetInteger(0, labelName, OBJPROP_COLOR, arrowColor);
      ObjectSetInteger(0, labelName, OBJPROP_FONTSIZE, 8);
   }
}

void SendSignalAlert(const SignalResult &signal)
{
   string message = StringFormat("%s %s %s signal: %d aligned signals (%s)",
                                 g_symbol, EnumToString(InpSignalTimeframe),
                                 DirectionText(signal.direction), signal.score, signal.reasons);

   Print(message);

   if(InpEnablePopupAlerts)
      Alert(message);

   if(InpEnableSoundAlerts)
      PlaySound(InpSoundFile);
}

string DirectionText(const int direction)
{
   if(direction == DIR_BUY)
      return "BUY";

   if(direction == DIR_SELL)
      return "SELL";

   return "NONE";
}
