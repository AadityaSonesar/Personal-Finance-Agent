import dotenv from 'dotenv';
dotenv.config({ path: '../.env' });
import mongoose from 'mongoose';
import axios from 'axios';
import jwt from 'jsonwebtoken';

async function main() {
  await mongoose.connect(process.env.MONGODB_URI);
  const db = mongoose.connection.db;
  const col = db.collection('transactions');

  const allTxs = await col.find({}).toArray();
  console.log(`Found ${allTxs.length} transactions`);

  const payload = allTxs.map(t => ({
    transaction_id: String(t._id),
    merchant: t.merchant,
    description: t.description || '',
    amount: t.amount,
    type: t.type
  }));

  const token = jwt.sign(
    { sub: 'test', email: 'test@test.com', scope: 'internal-ai', iss: 'aurelia-api', aud: 'aurelia-ai' },
    process.env.INTERNAL_SERVICE_SECRET,
    { expiresIn: '10m' }
  );

  console.log('Calling AI service...');
  const res = await axios.post('http://127.0.0.1:8000/internal/ai/categorize', { transactions: payload }, {
    headers: { Authorization: 'Bearer ' + token },
    timeout: 60000
  });

  const results = res.data.results || [];
  console.log(`AI returned ${results.length} results`);

  let count = 0;
  for (const r of results) {
    if (r.suggested_category) {
      await col.updateOne(
        { _id: new mongoose.Types.ObjectId(r.transaction_id) },
        {
          $set: {
            category: r.suggested_category,
            categorySource: 'ai',
            categoryConfidence: r.confidence || 0.85,
            aiSuggestion: r
          }
        }
      );
      count++;
    }
  }

  console.log(`Successfully updated ${count} transactions!`);

  const breakdown = await col.aggregate([
    { $group: { _id: '$category', count: { $sum: 1 } } },
    { $sort: { count: -1 } }
  ]).toArray();

  console.log('\nFinal DB breakdown:');
  for (const b of breakdown) {
    console.log(`  ${b._id}: ${b.count}`);
  }

  process.exit(0);
}

main().catch(err => {
  console.error('Error:', err.message);
  if (err.response) console.error(err.response.data);
  process.exit(1);
});
