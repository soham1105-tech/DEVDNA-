const { Router } = require('express');
const orders = require('../services/orders.js');

const router = Router();

router.post('/', orders.createOrder);
router.get('/:id', orders.getOrder);

module.exports = router;
