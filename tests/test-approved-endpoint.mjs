import assert from 'node:assert/strict';
import {onRequestGet} from '../functions/api/intelligence/approved.js';
const response=await onRequestGet({env:{}});
assert.equal(response.status,503);
assert.deepEqual(await response.json(),{error:'approved feed unavailable'});
const bad=await onRequestGet({env:{SUPABASE_URL:'http://localhost',SUPABASE_APPROVED_READ_KEY:'unused'}});
assert.equal(bad.status,503);
console.log('approved endpoint rejects missing and insecure configuration');
