-- Search customer text and use staff text only for explicit registration.
-- Priority weights and terms were fixed before running either arm.
WITH scores AS (
    SELECT case_id, tie,
      4 * (customer LIKE '%complain%' OR customer LIKE '%investigat%' OR customer LIKE '%ombudsman%') +
      3 * (customer LIKE '%without notice%' OR customer LIKE '%without consent%' OR customer LIKE '%without permission%' OR customer LIKE '%without warning%' OR customer LIKE '%unacceptable%' OR customer LIKE '%unauthori%') +
      2 * (customer LIKE '%third time%' OR customer LIKE '%third morning%' OR customer LIKE '%again%' OR customer LIKE '%keep %' OR customer LIKE '%twice%') +
      2 * (customer LIKE '%lost %' OR customer LIKE '%cost me%' OR customer LIKE '%out of pocket%' OR customer LIKE '%wrong%' OR customer LIKE '%missed%' OR customer LIKE '%charged me%') +
      2 * (customer LIKE '%unhappy%' OR customer LIKE '%upset%' OR customer LIKE '%distress%' OR customer LIKE '%disturb%' OR customer LIKE '%unsafe%' OR customer LIKE '%hardship%') +
      2 * (customer LIKE '%sort it%' OR customer LIKE '%put %right%' OR customer LIKE '%explain why%' OR customer LIKE '%supervisor%' OR customer LIKE '%manager%') +
      2 * ((staff LIKE '%refund%' OR staff LIKE '%revers%' OR staff LIKE '%credit%')
           AND (customer LIKE '%still%' OR customer LIKE '%but %' OR customer LIKE '%anyway%')) AS priority
    FROM conversations WHERE staff NOT LIKE '%complaint has been registered%'
) SELECT case_id, priority FROM scores WHERE priority > 0 ORDER BY priority DESC, tie
