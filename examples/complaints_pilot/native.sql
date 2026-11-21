-- A queue analyst can use the same history, redress notes and dated controls as Telryn.
WITH candidates AS (
    SELECT e.*,
           lower(message) AS body,
           lower(agent_note) LIKE '%refund issued%' AS refunded,
           prior_contacts > 0 AS repeated
    FROM eligible AS e
    WHERE NOT EXISTS (
        SELECT 1 FROM linked AS l
        JOIN register_at_cutoff AS r ON r.match_key = l.match_key
        WHERE l.case_id = e.case_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM linked AS l
        JOIN owners_at_cutoff AS a ON a.case_id = l.contact_id
        WHERE l.case_id = e.case_id AND a.status IN ('accepted', 'completed')
    )
), ranked AS (
    SELECT case_id,
           (CASE WHEN repeated THEN 4 ELSE 0 END)
         + (CASE WHEN body LIKE '%ombudsman%' OR body LIKE '%hardship%'
                       OR body LIKE '%medicine%' THEN 3 ELSE 0 END)
         + (CASE WHEN body LIKE '%complain%' OR body LIKE '%investigat%'
                       OR body LIKE '%third time%' THEN 2 ELSE 0 END)
         + (CASE WHEN refunded AND (body LIKE '%still%' OR body LIKE '%unhappy%')
                 THEN 2 ELSE 0 END) AS priority
    FROM candidates
)
SELECT case_id FROM ranked
WHERE priority > 0
ORDER BY priority DESC, case_id ASC
LIMIT :slots
