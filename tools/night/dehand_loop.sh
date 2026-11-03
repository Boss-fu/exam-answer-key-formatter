#!/bin/bash
# 去筆跡自動迭代：每輪從目前冠軍接著訓練，抓到率要比冠軍高、綜合分（抓到−3×誤標）不能比冠軍低，才升為新冠軍。
# 依序試 tools/night/dehand_plan.txt 的設定（每行：名稱 環境變數… 參數…）；沒贏就換下一個方向。到 END_AT 停。
cd "$(dirname "$0")/../.."
END_AT="${END_AT:-1300}"; P=~/.venvs/zizi-exam/bin/python; LOG=ocr/results/night/log.md; CH=ocr/results/night/dehand_champion.txt
[ -f $CH ] || echo "ocr/dehand/runs/jw-mask-r8/dehand_jwmask.pt" > $CH
echo "- $(date '+%m/%d %H:%M') 去筆跡：r8 門檻曲線" >> $LOG; $P tools/night/dehand_pr.py "$(cat $CH)" 2>/dev/null | sed 's/^/  - /' >> $LOG
while read -r name rest; do
  [ -z "$name" ] || [[ "$name" == \#* ]] && continue
  [ "$(date +%H%M)" -ge "$END_AT" ] && break
  out=ocr/dehand/runs/jw-mask-$name; [ -f $out/done ] && continue; mkdir -p $out
  champ=$(cat $CH)
  echo "- $(date '+%m/%d %H:%M') 去筆跡 $name 開始（起點 $champ；$rest）" >> $LOG
  env $(echo "$rest" | tr ' ' '\n' | grep '=' ) nice -n 10 $P ocr/dehand/finetune_jw_mask.py --out $out --init "$champ" $(echo "$rest" | tr ' ' '\n' | grep '^--' | tr '\n' ' ') > $out/train.log 2>&1
  touch $out/done
  v=$($P - "$out" <<'PY'
import json, glob, sys
js = glob.glob(sys.argv[1] + "/*.json")
if not js: print("FAIL 沒有輸出模型（沒有任何 epoch 贏過起點）"); sys.exit(1)
j = json.load(open(js[0])); b, f = j["base"], j["final"]
cb, cf = b[0] - 3 * (b[1] + b[2]), f[0] - 3 * (f[1] + f[2])
ok = f[0] > b[0] and cf >= cb
print(("PASS" if ok else "FAIL") + f" 抓到 {b[0]:.1%}→{f[0]:.1%} 印刷誤標 {b[1]:.2%}→{f[1]:.2%} 綜合 {cb:.3f}→{cf:.3f}"); sys.exit(0 if ok else 1)
PY
)
  if [[ "$v" == PASS* ]]; then echo "$out/dehand_jwmask.pt" > $CH; echo "- $(date '+%m/%d %H:%M') 🏆 去筆跡 $name 成為新冠軍：$v" >> $LOG
  else echo "- $(date '+%m/%d %H:%M') ✖ 去筆跡 $name 沒贏：$v" >> $LOG; fi
done < tools/night/dehand_plan.txt
echo "- $(date '+%m/%d %H:%M') 去筆跡迭代結束，冠軍 $(cat $CH)" >> $LOG
