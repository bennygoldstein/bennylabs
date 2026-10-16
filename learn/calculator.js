'use strict';
const form = document.querySelector('#cost-calculator');
const total = document.querySelector('#cost-total');
const breakdown = document.querySelector('#cost-breakdown');
const money = value => '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: value > 0 && value < 0.01 ? 8 : 2 });
function calculate() {
  const fields = ['input-tokens','input-rate','output-tokens','output-rate'].map(id => document.getElementById(id));
  const values = fields.map(field => field.valueAsNumber);
  if (fields.some(field => field.value.trim() === '') || values.some(value => !Number.isFinite(value) || value < 0) || !Number.isInteger(values[0]) || !Number.isInteger(values[2])) {
    total.textContent = 'Enter valid non-negative values. Token counts must be whole numbers.';
    breakdown.textContent = 'All four fields are required.';
    return;
  }
  const input = values[0] / 1000000 * values[1];
  const output = values[2] / 1000000 * values[3];
  if (!Number.isFinite(input + output)) {
    total.textContent = 'These values are too large. Enter smaller values.';
    breakdown.textContent = '';
    return;
  }
  total.textContent = 'Estimated token cost: ' + money(input + output) + ' USD';
  breakdown.textContent = 'Input: ' + money(input) + ' + output: ' + money(output);
}
form.addEventListener('submit', event => {event.preventDefault(); calculate();});
form.addEventListener('input', calculate);
form.addEventListener('reset', () => { setTimeout(calculate, 0); });
calculate();
