# tactician-workshop-20261101-100000
project: /tmp/workshop
started: 2026-11-01T10:00:00

  [10:00:00] You: Good morning everyone, welcome. Today we do bank reconciliations first, then month-end accruals, then your questions.
  [10:00:15] You: Quick show of hands in the chat: type A if you reconcile your bank accounts monthly, B if weekly.
  [10:00:45] You: Thanks, mostly A, that is what I expected.
  [10:01:00] You: So, bank reconciliations. You start from the bank statement balance and the general ledger cash balance.
  [10:01:30] You: The differences are usually outstanding cheques and deposits in transit.
  [10:02:00] You: An outstanding cheque is one you wrote and recorded, but the bank has not cleared yet.
  [10:02:30] You: A deposit in transit is cash you recorded on the last day that the bank posts the next morning.
  [10:03:00] You: The tool lists each difference, and labels its guess at the cause as a hypothesis, not a fact.
  [10:03:30] You: Your job is to check the hypothesis against the next month's bank statement.
  [10:04:00] You: Bank charges and interest are the other common items; they need an entry in the ledger.
  [10:04:30] You: Okay, that is bank reconciliations. Now let's move to month-end accruals.
  [10:04:45] You: An accrual records an expense you have incurred but not yet been invoiced for.
  [10:05:00] You: Think of the audit fee for work done in October, invoiced in November.
  [10:05:15] You: The tool builds an accrual schedule with draft entries, and every amount shows its source.
  [10:05:45] You: Priya asked in the chat what happens with invoices that arrive after the cutoff.
  [10:06:00] You: Good question. The tool flags an invoice dated after cutoff for work done in the month as a candidate accrual.
  [10:06:15] You: It does not book anything. You decide, and you post the entry yourself.
  [10:06:45] You: Next, roll-forwards of prepaid expenses.
  [10:07:15] You: Prepaid insurance is the classic one: the opening balance, plus new premiums, less the month's amortization.
  [10:07:45] You: The closing balance should tie to the trial balance.
  [10:08:15] You: If it does not tie, the tool says so and does not force it.
