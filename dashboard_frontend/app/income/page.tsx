"use client";

import { useEffect, useState } from "react";
import { getIncomeToday, getLast7DaysIncome, getToken, type IncomeDay } from "@/lib/api";

/** Simple tooltip component */
function Tooltip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-block">
      <button
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onFocus={() => setShow(true)}
        onBlur={() => setShow(false)}
        className="ml-1 inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-200 text-xs font-bold text-gray-600 hover:bg-gray-300"
        aria-label="Info"
      >
        ?
      </button>
      {show && (
        <span
          role="tooltip"
          className="absolute bottom-full left-1/2 z-10 mb-2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-900 px-3 py-1.5 text-xs text-white shadow-lg"
        >
          {text}
          <span className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-gray-900" />
        </span>
      )}
    </span>
  );
}

export default function IncomePage() {
  const [todayIncome, setTodayIncome] = useState<number | null>(null);
  const [todaySessions, setTodaySessions] = useState<number | null>(null);
  const [last7Days, setLast7Days] = useState<IncomeDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Check if user has a token
    if (!getToken()) {
      setError("Please sign in first. Redirecting…");
      setTimeout(() => {
        window.location.href = "/";
      }, 2000);
      setLoading(false);
      return;
    }

    async function fetchData() {
      try {
        const [today, week] = await Promise.all([
          getIncomeToday(),
          getLast7DaysIncome(),
        ]);
        setTodayIncome(today.total_usd);
        setTodaySessions(today.sessions_uploaded);
        setLast7Days(week);
      } catch (err: any) {
        setError(err.message ?? "Failed to load income data");
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-indigo-200 border-t-indigo-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-center text-red-700">
        <p className="font-semibold">Error loading data</p>
        <p className="mt-1 text-sm">{error}</p>
        <a href="/" className="mt-3 inline-block text-sm font-medium text-indigo-600 hover:underline">
          ← Back to sign in
        </a>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          Income Dashboard
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Track your earnings from game sessions
        </p>
      </div>

      {/* Today's summary cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-sm font-medium text-gray-500">
            Today&apos;s Earnings
            <Tooltip text="Total USD earned from sessions uploaded today" />
          </p>
          <p className="mt-2 text-3xl font-extrabold text-indigo-600">
            ${todayIncome?.toFixed(2) ?? "0.00"}
          </p>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-sm font-medium text-gray-500">
            Sessions Today
            <Tooltip text="Number of game sessions uploaded today" />
          </p>
          <p className="mt-2 text-3xl font-extrabold text-gray-900">
            {todaySessions ?? 0}
          </p>
        </div>
      </div>

      {/* Last 7 days table */}
      <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-5 py-4">
          <h2 className="text-lg font-semibold">Last 7 Days</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                <th className="px-5 py-3">Date</th>
                <th className="px-5 py-3">Sessions</th>
                <th className="px-5 py-3 text-right">Earned</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {last7Days.length === 0 ? (
                <tr>
                  <td
                    colSpan={3}
                    className="px-5 py-8 text-center text-gray-400"
                  >
                    No data yet — upload some sessions to get started!
                  </td>
                </tr>
              ) : (
                last7Days.map((day) => (
                  <tr key={day.date} className="hover:bg-gray-50 transition">
                    <td className="whitespace-nowrap px-5 py-3 font-medium text-gray-900">
                      {day.date}
                    </td>
                    <td className="px-5 py-3 text-gray-600">
                      {day.sessions_uploaded}
                    </td>
                    <td className="whitespace-nowrap px-5 py-3 text-right font-semibold text-green-600">
                      ${day.total_usd.toFixed(2)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* How payouts work */}
      <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-5 py-4">
        <h3 className="font-semibold text-indigo-800">
          How payouts work
          <Tooltip text="Payouts are processed weekly via Stripe" />
        </h3>
        <ul className="mt-2 space-y-1 text-sm text-indigo-700">
          <li>• Earnings accumulate from each uploaded game session</li>
          <li>• Payouts are processed every Monday for the previous week</li>
          <li>• Minimum payout threshold: $10.00</li>
          <li>• Funds arrive in your linked account within 2–3 business days</li>
        </ul>
      </div>
    </div>
  );
}
