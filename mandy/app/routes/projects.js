import Ember from 'ember';

export default Ember.Route.extend({
  activate() {
    // TODO: dynamically insert the correct socket url
    var socket = this.get('websockets').socketFor('ws://localhost:7777/ws');
    var target = this.store;

    socket.on('open', () => {
      console.log("sending proj request");
      socket.send({
        "method": "list",
        "collection": "projects"
      }, true);
    }, this);

    socket.on('message', (event) => {
      // fill the store with the data
      var update = JSON.parse(event.data);

      target.push(update);
    }, this);
  },
  model() {
    // return all records of the project model:
    return this.store.peekAll('project');
  }
});
