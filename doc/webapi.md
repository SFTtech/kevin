Web API Examples
================

Subscribe to a Build
--------------------

Request
```json
{
	"class": "SubscribeToBuild",
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
	"class": "ListProjects"
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
