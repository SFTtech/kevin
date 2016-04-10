Web API Examples
================

Subscribe to a Build
--------------------

Request
```json
{
	"project": " ... ",
	"commit_hash": " ... "
}
```

Respone
```json
{
	"success": true
}
```

List Projects
-------------

Request
```json
{
	"method": "list",
	"collection": "projects"
}
```

Respone
```json
{
	"data": [
		{
			"type": "project",
			"id": 5,
			"attributes": {
				"name": "Super Duper Project",
				"state": "dunno",
			},
		}
	]
}
```
