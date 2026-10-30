import { Router } from 'express';
import { listProducts, getProduct } from '../services/catalog.js';

export const router = Router();

router.get('/', listProducts);
router.get('/:id', getProduct);

export default router;
