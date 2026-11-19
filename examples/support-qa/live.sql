SELECT id
FROM requests
ORDER BY json_extract(inputs, '$.priority') DESC, id ASC
LIMIT :capacity
