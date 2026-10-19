"""Render the completed local comparison without publishing broker exports."""
import argparse
import csv
import html
import json
from pathlib import Path


NAMES = {
    "legacy_original_M15": "القديم الأصلي: M15 وخروج واحد",
    "legacy_matched_M5": "إشارات القديم مع خروج M5 موحّد",
    "advanced_matched_M5": "إشارات المتقدم مع خروج M5 موحّد",
    "advanced_structure_stop": "المتقدم بوقف خلف سحب السيولة",
}


def render(folder: Path):
    data = json.loads((folder / "comparison.json").read_text())
    lines = ["# مقارنة نموذج الذهب المتقدم بالنموذج القديم", "",
        "الفترة: 20 أبريل إلى 19 أكتوبر 2026، بتوقيت خادم MT5. الفترة اللاحقة: 20 يوليو إلى 19 أكتوبر.", "",
        "**النتيجة: التطوير يعمل تقنيًا، لكنه لم يثبت أفضلية ربح مستقرة. لا توجد هنا نسبة نجاح متوقعة للصفقة التالية.**", "",
        "| النموذج | الصفقات | النتيجة R | معامل الربح | أكبر تراجع R | نتيجة الفترة اللاحقة R |",
        "|---|---:|---:|---:|---:|---:|"]
    for key, name in NAMES.items():
        row = data["arms"][key]
        a, later = row["all"], row["later"]
        lines.append(f"| {name} | {a['trades']} | {a['total_r']:+.3f} | {a['profit_factor']} | {a['max_drawdown_r']:.3f} | {later['total_r']:+.3f} |")
    lines += ["", "## معنى المقارنة", "",
        "المقارنة التي تعزل أثر إشارات الدخول هي الصف الثاني مقابل الثالث: نفس شموع M5، ونفس وقف 1.5 ATR، وثلاثة أهداف 1R و2R و3R بأوزان متساوية، ومدة قصوى ساعتين، ومركز واحد في الوقت نفسه. الصف الأول يعيد قواعد القديم بخروجه الأصلي الأطول؛ فلا يُستخدم وحده لعزل أثر الإشارات. الصف الرابع يضيف الوقف الهيكلي، ولذلك يقيس تغيير النظام كله.", "",
        "R هي وحدة مخاطرة الصفقة، وليست عائد الحساب أو مبلغًا بالدولار. الأرقام تتضمن نفس افتراضات الاحتكاك التاريخي لكل النماذج. لا تتوافر عمولات أو تمويل ليلي أو تيكات تاريخية كاملة.", "",
        "## ما تم تطويره", "",
        "- قمم وقيعان لا تصبح معروفة إلا بعد شمعتَي تأكيد، مع BOS وCHOCH.",
        "- سحب سيولة من مستوى مؤكد أو من اليوم السابق، ثم كسر هيكل خلال مدة محددة.",
        "- فجوات ذات حركة اندفاع مقاسة بـATR؛ مع أول لمس، وامتلاء، وإلغاء، وانتهاء صلاحية. لا تُنشأ فجوة من شموع تفصلها فترة إغلاق السوق.",
        "- فيبوناتشي من قمم وقيعان مؤكدة على الشارت. إشارة النموذج تستخدم موجة السحب والاندفاع المجمدة عند تكوّن الفجوة، ضمن نطاق ارتداد 38.2–78.6%.",
        "- سياق H1/M15 من الشموع المكتملة فقط، ووقف خلف قمة/قاع سحب السيولة وثلاثة أهداف R.",
        "- تذبذب نسبي ونشاط تيكات وملف توزيع نشاط تقريبي. هذه ليست دلتا صفقات أو Volume Profile بورصيًا. بيانات الدلتا الفعلية غير متصلة.",
        "- سجل أدلة لكل إشارة ورفض، وتقسيم شهري وساعي وحسب التذبذب، ونطاق تشخيصي للعائد المتوقع.", "",
        "## صلاحية الدليل", ""]
    for key in ("legacy_matched_M5", "advanced_matched_M5", "advanced_structure_stop"):
        s = data["arms"][key]["all"]
        lines.append(f"- {NAMES[key]}: متوسط الصفقة {s['mean_r']}R؛ نطاق إعادة المعاينة التشخيصي 95% هو {s['expectancy_bootstrap_95']}R.")
    lines += ["", "هذه النطاقات تشمل الصفر، وإعادة المعاينة لا تعالج الاعتماد الزمني بين الصفقات. لم تُجرَ عملية بحث عن أفضل إعداد على الأشهر الستة؛ قواعد الدخول ثابتة. هذا اختبار تاريخي على الشموع، وليس تجربة مستقبلية مستقلة، ولا تشغيلًا للفريق اللغوي أو اختبارًا رسميًا داخل MT5 Strategy Tester.", "",
        "إذا لمس الوقف والهدف الشمعة نفسها، يحتسب الوقف أولًا. إشارات الإغلاق تدخل عند افتتاح الشمعة التالية فقط؛ ولا تُفترض تعبئة أمر داخل فترة إغلاق السوق. قياسات أقصى حركة ضد/مع الصفقة حدود من الشموع، وليست ترتيب تيكات مؤكّدًا.", "",
        "## جودة البيانات والتوقيت", "",
        "التصدير الجديد يحفظ توقيت الخادم كما هو؛ لم تُفرض عليه تسمية UTC. الإزاحات التاريخية للخادم لم تُراجع مستقلاً، لذلك التقسيم الساعي بتوقيت الخادم فقط. القيم OHLC صحيحة والشموع مرتبة وفريدة. الفجوات الزمنية تشمل فترات الإغلاق وأي شموع ناقصة، ولم تصنّف بالكامل بتقويم جلسات مستقل.", "",
        f"بصمة مجموعة البيانات: `{data['provenance']['sha256_ohlc_dataset']}`.", "",
        "ملفات الصفقات وأدلة الإشارة وبيانات الوسيط محفوظة محليًا داخل results، وغير مضافة إلى Git."]
    text = "\n".join(lines) + "\n"
    (folder / "تقرير-المقارنة.md").write_text(text, encoding="utf-8")
    rows = []
    for key, name in NAMES.items():
        a=data["arms"][key]
        rows.append("<tr>"+"".join(f"<td>{html.escape(str(x))}</td>" for x in
                    [name,a["all"]["trades"],a["all"]["total_r"],a["all"]["profit_factor"],a["all"]["max_drawdown_r"],a["later"]["total_r"]])+"</tr>")
    plot = ""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from datetime import datetime
        fig,ax=plt.subplots(figsize=(10,4.7))
        for key,label in [("legacy_matched_M5","Legacy entries / matched exits"),
                          ("advanced_matched_M5","Advanced entries / matched exits"),
                          ("advanced_structure_stop","Advanced / structure stop")]:
            trades=list(csv.DictReader((folder/f"{key}.csv").open()))
            total=0.;ys=[]
            for t in trades:total+=float(t["net_r"]);ys.append(total)
            ax.step([datetime.fromisoformat(t["exit_time_server"]) for t in trades],ys,where="post",label=label)
        ax.axhline(0,color="gray",lw=.6);ax.set_ylabel("Cumulative R");ax.set_xlabel("MT5 server date")
        ax.set_title("XAUUSD: fixed rules, 20 Apr - 19 Oct 2026");ax.legend(fontsize=8)
        ax.grid(alpha=.2);fig.tight_layout();fig.savefig(folder/"comparison-equity.png",dpi=170);plt.close(fig)
        plot='<img src="comparison-equity.png" alt="Cumulative R comparison">'
    except ImportError:
        pass
    doc=f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>مقارنة نموذج الذهب</title>
    <style>body{{font:17px Arial;max-width:1150px;margin:40px auto;padding:0 24px;background:#f5f7fa;color:#172333;line-height:1.8}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:12px;border-bottom:1px solid #dde2ea;text-align:right}}.box{{background:#fff0dd;padding:18px;border-radius:10px}}img{{width:100%;margin-top:25px}}pre{{white-space:pre-wrap;font:15px Arial;background:white;padding:24px}}</style>
    <h1>اختبار نموذج الذهب المتقدم</h1><p>20 أبريل — 19 أكتوبر 2026 | بيانات MT5 الفعلية بتوقيت الخادم</p>
    <p class="box">النموذج المتقدم لم يثبت أفضلية ربح مستقرة. إضافته إلى الشارت لا تعني أن جودة الصفقات تحسنت إحصائيًا.</p>
    <table><tr><th>النموذج</th><th>صفقات</th><th>نتيجة R</th><th>معامل الربح</th><th>أكبر تراجع R</th><th>الفترة اللاحقة R</th></tr>{''.join(rows)}</table>{plot}
    <pre>{html.escape(text)}</pre></html>'''
    (folder/"comparison.html").write_text(doc,encoding="utf-8")
    print(folder/"comparison.html")


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("folder",type=Path);a=p.parse_args();render(a.folder)
