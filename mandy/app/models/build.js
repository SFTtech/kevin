import DS from 'ember-data';

export default DS.Model.extend({
  hash: DS.attr("string"),
  number: DS.attr("string"),
  init_time: DS.attr("date"),
  state: DS.attr("string"),
  duration: DS.attr("number"),
});
